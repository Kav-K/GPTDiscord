"""Transcription command contracts without media downloads or provider calls."""

import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.test_user_api_key import text_service  # noqa: F401


@pytest.fixture
def transcriber(text_service, monkeypatch):
    module = importlib.import_module("cogs.transcription_service_cog")
    monkeypatch.setattr(module, "USER_INPUT_API_KEYS", False)
    model = SimpleNamespace(
        send_transcription_request=AsyncMock(return_value="Transcript")
    )
    return module, SimpleNamespace(model=model)


def context():
    progress = SimpleNamespace(edit=AsyncMock(), delete=AsyncMock())
    ctx = SimpleNamespace(
        user=SimpleNamespace(id=42),
        defer=AsyncMock(),
        respond=AsyncMock(return_value=progress),
        interaction=object(),
    )
    return ctx, progress


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filename,mime",
    [
        ("AUDIO.WAV", None),
        ("voice.ogg", "audio/ogg"),
        ("voice.flac", "application/octet-stream"),
    ],
)
async def test_supported_audio_does_not_require_discord_mime_metadata(
    transcriber, filename, mime
):
    module, cog = transcriber
    ctx, progress = context()
    attachment = SimpleNamespace(filename=filename, content_type=mime)

    await module.TranscribeService.transcribe_file_command(cog, ctx, attachment, 0)

    cog.model.send_transcription_request.assert_awaited_once_with(attachment, 0, None)
    assert "Transcript" in progress.edit.await_args.kwargs["embed"].description


@pytest.mark.asyncio
async def test_long_transcript_is_delivered_once_without_oversized_success_embed(
    transcriber, monkeypatch
):
    module, cog = transcriber
    transcript = "Long transcript " * 800
    cog.model.send_transcription_request.return_value = transcript
    paginators = []

    def paginator(**kwargs):
        result = SimpleNamespace(**kwargs, respond=AsyncMock())
        paginators.append(result)
        return result

    monkeypatch.setattr(module.pages, "Paginator", paginator)
    ctx, progress = context()
    await module.TranscribeService.transcribe_file_command(
        cog,
        ctx,
        SimpleNamespace(filename="voice.mp3", content_type="audio/mpeg"),
        0,
    )

    assert "".join(page.description for page in paginators[0].pages) == transcript
    assert all(len(page.description) <= 3500 for page in paginators[0].pages)
    paginators[0].respond.assert_awaited_once_with(ctx.interaction)
    progress.delete.assert_awaited_once()
    progress.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_user_youtube_requests_have_separate_cleaned_up_media(
    transcriber, monkeypatch
):
    module, cog = transcriber
    monkeypatch.setattr(
        module.UrlCheck, "check_youtube_link", AsyncMock(return_value=True)
    )
    paths, files = [], []
    both_started = asyncio.Event()

    class Downloader:
        def __init__(self, options):
            self.path = Path(options["outtmpl"].replace("%(ext)s", "mp3"))
            assert options["noplaylist"] is True

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def download(self, urls):
            self.path.write_bytes(b"fake audio")

    async def transcribe(file, temperature, key):
        paths.append(Path(file.fp.name))
        files.append(file)
        if len(paths) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), 1)
        assert paths[0].exists() and paths[1].exists()
        return "Transcript"

    monkeypatch.setattr(module, "YoutubeDL", Downloader)
    cog.model.send_transcription_request.side_effect = transcribe
    await asyncio.gather(
        *(
            module.TranscribeService.transcribe_link_command(
                cog, context()[0], "https://youtu.be/test", 0
            )
            for _ in range(2)
        )
    )

    assert paths[0] != paths[1]
    assert all(not path.parent.exists() for path in paths)
    assert all(file.fp.closed for file in files)


@pytest.mark.asyncio
async def test_transcription_errors_do_not_echo_provider_bodies(transcriber):
    module, cog = transcriber
    from models.openai_model import OpenAIAPIError

    cog.model.send_transcription_request.side_effect = OpenAIAPIError(
        "PRIVATE provider body", 401
    )
    ctx, progress = context()
    await module.TranscribeService.transcribe_file_command(
        cog,
        ctx,
        SimpleNamespace(filename="voice.mp3", content_type=None),
        0,
    )
    error = progress.edit.await_args.kwargs["embed"].description
    assert "PRIVATE" not in error
    assert "key" in error.lower()
