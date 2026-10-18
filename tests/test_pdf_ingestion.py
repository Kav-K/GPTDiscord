"""Real PDF parsing with a bounded, simulated HTTP download boundary."""

import asyncio
import importlib
import io
from pathlib import Path

import pytest

pytest.importorskip("llama_index.core")
pytest.importorskip("llama_index.llms.openai")
pytest.importorskip("llama_index.embeddings.openai")
pytest.importorskip("llama_index.readers.file")

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

MAX_BYTES = 25 * 1024 * 1024
URL = "https://example.org/fixture.pdf"


def pdf_bytes(*, text="The Atlas team maintains the Helios archive.", pages=1):
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=300, height=300)
        if text:
            page[NameObject("/Resources")] = DictionaryObject(
                {
                    NameObject("/Font"): DictionaryObject(
                        {
                            NameObject("/F1"): DictionaryObject(
                                {
                                    NameObject("/Type"): NameObject("/Font"),
                                    NameObject("/Subtype"): NameObject("/Type1"),
                                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                                }
                            )
                        }
                    )
                }
            )
            stream = DecodedStreamObject()
            stream.set_data(f"BT /F1 12 Tf 20 250 Td ({text}) Tj ET".encode())
            page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class Response:
    def __init__(self, chunks, headers=None, error=None):
        self.headers = headers or {}
        self.chunks = chunks
        self.error = error
        self.content = self
        self.read_chunks = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def raise_for_status(self):
        pass

    async def iter_chunked(self, size):
        assert size == 64 * 1024
        for chunk in self.chunks:
            if self.error:
                raise self.error
            self.read_chunks += 1
            yield chunk


@pytest.fixture
def reader(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SHARE_DIR", str(tmp_path))
    service = importlib.import_module("services.indexing_service")
    original = service.tempfile.TemporaryDirectory
    directories = []

    def temporary_directory():
        temporary = original(dir=tmp_path)
        directories.append(Path(temporary.name))
        return temporary

    monkeypatch.setattr(service.tempfile, "TemporaryDirectory", temporary_directory)
    monkeypatch.setattr(
        service.EnvService, "get_http_proxy", lambda url: "http://proxy.invalid:8080"
    )
    yield service, directories
    assert directories and not any(path.exists() for path in directories)


def install_http(monkeypatch, service, response):
    requests = []

    class Session:
        def __init__(self, **kwargs):
            requests.append(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def get(self, url, **kwargs):
            requests.append({"url": url, **kwargs})
            return response

    monkeypatch.setattr(service.aiohttp, "ClientSession", Session)
    return requests


@pytest.mark.asyncio
@pytest.mark.parametrize("content_length", [False, True])
async def test_actual_pdf_text_and_metadata_survive_streamed_download(
    reader, monkeypatch, content_length
):
    service, _ = reader
    data = pdf_bytes()
    headers = {"Content-Length": str(len(data))} if content_length else {}
    response = Response([data[:30], data[30:]], headers=headers)
    requests = install_http(monkeypatch, service, response)

    documents = await service.read_pdf_documents(URL)

    assert len(documents) == 1
    assert "The Atlas team maintains the Helios archive." in documents[0].get_content()
    assert documents[0].metadata["URL"] == URL
    assert documents[0].metadata["page_label"] == "1"
    assert response.read_chunks == 2
    assert requests[0]["timeout"].total == 30
    assert requests[1] == {"url": URL, "proxy": "http://proxy.invalid:8080"}
    assert "ssl" not in requests[0] and "ssl" not in requests[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("pages", [0, 1])
async def test_real_empty_or_scanned_pdf_gives_text_export_guidance(
    reader, monkeypatch, pages
):
    service, _ = reader
    install_http(monkeypatch, service, Response([pdf_bytes(text="", pages=pages)]))

    with pytest.raises(ValueError, match="no extractable text.*OCR.*text export"):
        await service.read_pdf_documents(URL)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body", [b"", b"<html>Not a PDF</html>", b"%PDF-1.7\nnot a valid PDF structure"]
)
async def test_wrong_or_malformed_pdf_never_becomes_an_empty_index(
    reader, monkeypatch, body
):
    service, _ = reader
    install_http(
        monkeypatch,
        service,
        Response([body], headers={"Content-Type": "application/pdf"}),
    )

    with pytest.raises(ValueError, match="valid PDF|Invalid PDF"):
        await service.read_pdf_documents(URL)


@pytest.mark.asyncio
async def test_advertised_oversize_pdf_is_rejected_before_reading_body(
    reader, monkeypatch
):
    service, _ = reader
    response = Response(
        [b"not downloaded"], headers={"Content-Length": str(MAX_BYTES + 1)}
    )
    install_http(monkeypatch, service, response)

    with pytest.raises(ValueError, match="25 MiB or smaller"):
        await service.read_pdf_documents(URL)

    assert response.read_chunks == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("content_length", [None, "1"])
async def test_chunked_or_misreported_body_still_has_a_hard_size_limit(
    reader, monkeypatch, content_length
):
    service, _ = reader
    chunk = b"x" * (64 * 1024)
    response = Response(
        [chunk] * (MAX_BYTES // len(chunk)) + [b"x"],
        headers={} if content_length is None else {"Content-Length": content_length},
    )
    install_http(monkeypatch, service, response)

    with pytest.raises(ValueError, match="25 MiB or smaller"):
        await service.read_pdf_documents(URL)

    assert response.read_chunks == MAX_BYTES // len(chunk) + 1


@pytest.mark.asyncio
async def test_stalled_download_fails_and_removes_private_download(reader, monkeypatch):
    service, _ = reader
    install_http(
        monkeypatch, service, Response([b"%PDF-"], error=asyncio.TimeoutError())
    )

    with pytest.raises(asyncio.TimeoutError):
        await service.read_pdf_documents(URL)


@pytest.mark.asyncio
async def test_short_pdf_preamble_is_accepted(reader, monkeypatch):
    service, _ = reader
    install_http(monkeypatch, service, Response([b"\n" + pdf_bytes()]))

    documents = await service.read_pdf_documents(URL)

    assert "Helios archive" in documents[0].get_content()
