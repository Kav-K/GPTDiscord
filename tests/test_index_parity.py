"""Document and media parity through real indexes and simulated provider boundaries."""

import importlib
import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("llama_index.core")
pytest.importorskip("llama_index.llms.openai")
pytest.importorskip("langchain_openai")

from llama_index.core import Document
from PIL import Image

from tests.test_indexing import integrations, mock_context  # noqa: F401
from tests.test_conversation_recovery import recovery, thread_payload  # noqa: F401


def contents(index):
    return " ".join(node.get_content() for node in index.docstore.docs.values())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url,route,argument",
    [
        (
            "https://docs.google.com/document/d/doc_id-123/edit?usp=sharing",
            "index_gdoc",
            "doc_id-123",
        ),
        (
            "https://www.github.com/owner/project",
            "index_github_repository",
            "https://www.github.com/owner/project",
        ),
        (
            "https://youtu.be/abcdefghijk?si=sharing",
            "index_youtube_transcript",
            "https://youtu.be/abcdefghijk?si=sharing",
        ),
        (
            "https://example.com/github/report",
            "index_webpage",
            "https://example.com/github/report",
        ),
        (
            "https://github.com.example.com/report",
            "index_webpage",
            "https://github.com.example.com/report",
        ),
    ],
)
async def test_link_dispatch_uses_hostnames_and_exposes_google_docs(
    integrations, monkeypatch, url, route, argument
):
    module = importlib.import_module("models.index_model")
    handler = module.Index_handler(None, None)
    calls = []

    def reader(link, context):
        calls.append((link, context))
        return "index"

    for name in ("index_gdoc", "index_github_repository", "index_youtube_transcript"):
        monkeypatch.setattr(
            handler,
            name,
            reader
            if name == route
            else lambda *args: pytest.fail("Wrong specialized reader"),
        )
    monkeypatch.setattr(
        handler,
        "index_webpage",
        AsyncMock(side_effect=reader)
        if route == "index_webpage"
        else AsyncMock(side_effect=AssertionError("Unexpected webpage reader")),
    )
    context = object()

    index, summary = await handler.index_link(url, context=context)

    assert index == "index"
    assert summary is None
    assert calls == [(argument, context)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "https://docs.google.com/spreadsheets/d/id/edit",
        "github.com/owner/project",
    ],
)
async def test_invalid_or_unsupported_specialized_links_fail_before_fetch(
    integrations, url
):
    module = importlib.import_module("models.index_model")
    handler = module.Index_handler(None, None)
    with pytest.raises(ValueError, match="HTTP|Google Docs"):
        await handler.index_link(url, context=object())


@pytest.mark.parametrize(
    "url",
    [
        "https://youtu.be/abcdefghijk?si=link",
        "https://www.youtube.com/watch?v=abcdefghijk&t=3",
        "https://m.youtube.com/shorts/abcdefghijk",
        "https://youtube.com/live/abcdefghijk",
        "https://www.youtube-nocookie.com/embed/abcdefghijk",
    ],
)
def test_youtube_video_url_normalization(url):
    module = importlib.import_module("models.index_model")
    assert (
        module.Index_handler.youtube_link(url)
        == "https://www.youtube.com/watch?v=abcdefghijk"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://youtube.com/@channel",
        "https://youtube.com/playlist?list=abc",
        "https://youtube.com.example.com/watch?v=abcdefghijk",
        "https://youtu.be/invalid",
    ],
)
def test_youtube_channels_playlists_and_spoofed_hosts_are_not_transcripts(url):
    module = importlib.import_module("models.index_model")
    with pytest.raises(ValueError, match="video link"):
        module.Index_handler.youtube_link(url)


@pytest.mark.asyncio
@pytest.mark.parametrize("fails", [False, True])
async def test_youtube_reader_uses_current_transcript_sdk(
    integrations, monkeypatch, fails
):
    transcript_api = pytest.importorskip("youtube_transcript_api")
    module = importlib.import_module("models.index_model")
    calls = []
    sessions, closed = [], []
    original_close = module.requests.Session.close

    def request(session, method, url, **kwargs):
        assert kwargs["timeout"] == 15
        assert session.headers["Accept-Language"] == "en-US"
        sessions.append(session)
        if fails:
            raise module.requests.Timeout("Simulated timeout")
        return module.requests.Response()

    def close(session):
        closed.append(session)
        original_close(session)

    def fetch(self, video_id):
        calls.append(video_id)
        self._fetcher._http_client.get("https://youtube.com/watch?v=" + video_id)
        return transcript_api.FetchedTranscript(
            snippets=[
                transcript_api.FetchedTranscriptSnippet(
                    text="Video codename FENNEL", start=0.0, duration=2.0
                )
            ],
            video_id=video_id,
            language="English",
            language_code="en",
            is_generated=False,
        )

    monkeypatch.setattr(transcript_api.YouTubeTranscriptApi, "fetch", fetch)
    monkeypatch.setattr(module.requests.Session, "request", request)
    monkeypatch.setattr(module.requests.Session, "close", close)
    handler = module.Index_handler(None, None)
    task = asyncio.to_thread(
        handler.index_youtube_transcript,
        "https://youtu.be/abcdefghijk?t=4",
        mock_context(integrations),
    )
    if fails:
        with pytest.raises(ValueError, match="transcript is unavailable"):
            await task
    else:
        assert "FENNEL" in contents(await task)
    assert calls == ["abcdefghijk"]
    assert closed == sessions


class TextExport:
    def __init__(
        self,
        status=200,
        content_type="text/plain; charset=utf-8",
        chunks=(b"Google document codename FENNEL",),
    ):
        self.status_code = status
        self.headers = {"Content-Type": content_type}
        self.chunks = chunks
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def iter_content(self, chunk_size):
        assert chunk_size == 65536
        return iter(self.chunks)


@pytest.mark.asyncio
async def test_public_google_doc_export_is_queryable_without_oauth(
    integrations, monkeypatch
):
    module = importlib.import_module("models.index_model")
    handler = module.Index_handler(None, None)
    response = TextExport()
    requests = []

    def get(url, **kwargs):
        requests.append((url, kwargs))
        return response

    monkeypatch.setattr(module.requests, "get", get)
    context = mock_context(integrations)
    index = await asyncio.to_thread(handler.index_gdoc, "public_id", context)

    assert "FENNEL" in contents(index)
    assert response.closed
    assert requests == [
        (
            "https://docs.google.com/document/d/public_id/export?format=txt",
            {"timeout": 20, "stream": True},
        )
    ]
    assert context.token_counter.total_embedding_token_count > 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response,error",
    [
        (TextExport(status=403), "shared"),
        (TextExport(content_type="text/html"), "shared"),
        (TextExport(chunks=(b" ",)), "no readable text"),
        (TextExport(chunks=(b"x" * (10 * 1024 * 1024 + 1),)), "10 MiB"),
    ],
)
async def test_google_docs_access_and_size_failures_never_embed(
    integrations, monkeypatch, response, error
):
    module = importlib.import_module("models.index_model")
    monkeypatch.setattr(module.requests, "get", lambda *args, **kwargs: response)
    context = mock_context(integrations)
    handler = module.Index_handler(None, None)
    with pytest.raises(ValueError, match=error):
        await asyncio.to_thread(handler.index_gdoc, "id", context)
    assert context.token_counter.total_embedding_token_count == 0
    assert response.closed


@pytest.mark.asyncio
async def test_github_reader_uses_actual_default_branch_and_current_token(
    integrations, monkeypatch
):
    module = importlib.import_module("models.index_model")
    context = mock_context(integrations)
    handler = module.Index_handler(None, None)
    calls = []
    monkeypatch.setattr(module.EnvService, "get_github_token", lambda: "reader-token")

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(
            status_code=200, json=lambda: {"default_branch": "develop"}
        )

    class Client:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    class Reader:
        def __init__(self, client, **kwargs):
            assert isinstance(client, Client)
            calls.append(("github-client", kwargs))

        def load_data(self, **kwargs):
            calls.append(kwargs)
            return [Document(text="Repository codename FENNEL")]

    monkeypatch.setattr(module.requests, "get", get)
    monkeypatch.setitem(
        sys.modules,
        "llama_index.readers.github",
        SimpleNamespace(GithubClient=Client, GithubRepositoryReader=Reader),
    )
    index = await asyncio.to_thread(
        handler.index_github_repository, "https://github.com/owner/project.git", context
    )
    assert "FENNEL" in contents(index)
    assert calls == [
        (
            "https://api.github.com/repos/owner/project",
            {
                "headers": {
                    "Authorization": "Bearer reader-token",
                    "Accept": "application/vnd.github+json",
                },
                "timeout": 15,
            },
        ),
        {"github_token": "reader-token"},
        ("github-client", {"owner": "owner", "repo": "project"}),
        {"branch": "develop"},
    ]


def media_context(integrations, key="personal-key"):
    context = mock_context(integrations)
    return SimpleNamespace(
        llm=SimpleNamespace(model="gpt-6.1-sol"),
        embed_model=SimpleNamespace(api_key=key),
        index_kwargs=context.index_kwargs,
        token_counter=context.token_counter,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "suffix", [".mp3", ".wav", ".mp4", ".m4a", ".webm", ".flac", ".ogg"]
)
async def test_media_index_transcribes_with_request_key_and_closes_file(
    integrations, tmp_path, suffix
):
    module = importlib.import_module("models.index_model")
    path = tmp_path / f"audio{suffix}"
    path.write_bytes(b"Simulated media bytes")
    files = []

    async def transcribe(file, *, custom_api_key):
        assert custom_api_key == "personal-key"
        assert file.filename == f"user-recording{suffix}"
        assert file.fp.read() == b"Simulated media bytes"
        files.append(file.fp)
        return "The recording codename is FENNEL."

    model = SimpleNamespace(
        send_transcription_request=AsyncMock(side_effect=transcribe)
    )
    handler = module.Index_handler(None, None, model=model)
    context = media_context(integrations)
    index = await handler.index_uploaded_file(
        path, context, suffix, f"user-recording{suffix}"
    )

    assert "FENNEL" in contents(index)
    assert all(
        "video frames are not indexed" in node.metadata["extraction"]
        for node in index.docstore.docs.values()
    )
    assert files[0].closed
    assert context.token_counter.total_embedding_token_count > 0


@pytest.mark.asyncio
async def test_image_index_uses_bounded_native_vision_with_request_key(
    integrations, tmp_path
):
    module = importlib.import_module("models.index_model")
    path = tmp_path / "source.png"
    Image.new("RGB", (4, 4), "green").save(path)
    model = SimpleNamespace(
        send_request=AsyncMock(
            return_value={
                "choices": [{"message": {"content": "The image label says FENNEL."}}]
            }
        )
    )
    handler = module.Index_handler(None, None, model=model)
    context = media_context(integrations)

    index = await handler.index_uploaded_file(path, context, ".png", "diagram.png")

    assert "FENNEL" in contents(index)
    args, kwargs = model.send_request.await_args
    assert args[0][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert kwargs == {
        "model": "gpt-6.1-sol",
        "max_tokens_override": 4096,
        "custom_api_key": "personal-key",
    }
    assert context.token_counter.total_embedding_token_count > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix,megabytes", [(".png", 11), (".mp3", 26)])
async def test_oversize_media_never_reaches_provider_or_embedding(
    integrations, tmp_path, suffix, megabytes
):
    module = importlib.import_module("models.index_model")
    path = tmp_path / f"source{suffix}"
    with path.open("wb") as output:
        output.truncate(megabytes * 1024 * 1024)
    model = SimpleNamespace(
        send_request=AsyncMock(), send_transcription_request=AsyncMock()
    )
    handler = module.Index_handler(None, None, model=model)
    context = media_context(integrations)
    with pytest.raises(ValueError, match="indexing limit"):
        await handler.index_uploaded_file(path, context, suffix, path.name)
    model.send_request.assert_not_awaited()
    model.send_transcription_request.assert_not_awaited()
    assert context.token_counter.total_embedding_token_count == 0


@pytest.mark.asyncio
async def test_media_provider_failure_closes_file_and_never_embeds(
    integrations, tmp_path
):
    module = importlib.import_module("models.index_model")
    path = tmp_path / "source.wav"
    path.write_bytes(b"Simulated audio")
    files = []

    async def transcribe(file, **kwargs):
        files.append(file.fp)
        raise ValueError("Provider unavailable")

    model = SimpleNamespace(
        send_transcription_request=AsyncMock(side_effect=transcribe)
    )
    handler = module.Index_handler(None, None, model=model)
    context = media_context(integrations)
    with pytest.raises(ValueError, match="Provider unavailable"):
        await handler.index_uploaded_file(path, context, ".wav", "recording.wav")
    assert files[0].closed
    assert context.token_counter.total_embedding_token_count == 0


@pytest.mark.asyncio
async def test_concurrent_media_extractions_keep_personal_credentials_separate(
    integrations, tmp_path
):
    module = importlib.import_module("models.index_model")
    path = tmp_path / "source.wav"
    path.write_bytes(b"Simulated audio")

    async def transcribe(file, *, custom_api_key):
        await asyncio.sleep(0)
        return f"Recording for {custom_api_key}"

    model = SimpleNamespace(
        send_transcription_request=AsyncMock(side_effect=transcribe)
    )
    handler = module.Index_handler(None, None, model=model)
    first, second = await asyncio.gather(
        handler.index_uploaded_file(
            path, media_context(integrations, "first-account"), ".wav", "first.wav"
        ),
        handler.index_uploaded_file(
            path, media_context(integrations, "second-account"), ".wav", "second.wav"
        ),
    )
    assert "first-account" in contents(first)
    assert "second-account" not in contents(first)
    assert "second-account" in contents(second)
    assert "first-account" not in contents(second)


@pytest.mark.asyncio
async def test_data_chat_worker_preserves_single_attempt_request_policy(integrations):
    module = importlib.import_module("models.index_model")
    from services.request_context import single_attempt_requests

    def invoke(inputs):
        assert single_attempt_requests.get() is True
        return {"output": "A data answer"}

    usage = SimpleNamespace(update_usage_memory=lambda *args: None)
    handler = module.Index_handler(None, usage)
    handler.index_chat_chains[5] = SimpleNamespace(
        agent_chain=SimpleNamespace(invoke=invoke)
    )
    message = SimpleNamespace(
        channel=SimpleNamespace(id=5), guild=SimpleNamespace(name="Test")
    )
    token = single_attempt_requests.set(True)
    try:
        assert (
            await handler.execute_index_chat_message(message, "A question")
            == "A data answer"
        )
    finally:
        single_attempt_requests.reset(token)


@pytest.mark.asyncio
async def test_data_chat_extracts_only_the_url_from_a_natural_language_message(
    integrations, monkeypatch
):
    from tests.test_index_features import index_session

    module, cog, message = index_session(integrations, monkeypatch)
    message.content = (
        "Please explain https://example.com/report and compare the findings."
    )
    cog.process_indexing = AsyncMock(return_value=True)
    await cog.on_message(message)
    cog.process_indexing.assert_awaited_once_with(
        message, "link", link="https://example.com/report"
    )


@pytest.mark.asyncio
async def test_index_rename_uses_shared_storage_and_preserves_existing_destination(
    integrations, tmp_path, monkeypatch
):
    module = importlib.import_module("models.index_model")
    source = tmp_path / "indexes" / "42" / "original"
    source.mkdir(parents=True)
    (source / "docstore.json").write_text("retained-index")
    elsewhere = tmp_path / "unrelated-working-directory"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    handler = module.Index_handler(None, None)

    assert await handler.rename_index(None, "indexes/42/original", "indexes/42/renamed")
    assert (source.parent / "renamed" / "docstore.json").read_text() == "retained-index"
    replacement = source.parent / "occupied"
    replacement.mkdir()
    assert not await handler.rename_index(
        None, "indexes/42/renamed", "indexes/42/occupied"
    )
    assert (source.parent / "renamed" / "docstore.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name", ["../other-owner", "nested/name", r"nested\name", ".", ".."]
)
async def test_composed_index_names_cannot_escape_owner_storage(integrations, name):
    module = importlib.import_module("models.index_model")
    handler = module.Index_handler(None, None)
    with pytest.raises(ValueError, match="single file name"):
        await handler.compose_indexes(42, [], name, False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target",
    ["indexes/43/stolen", "indexes/42/nested/name", "indexes/42/../../elsewhere"],
)
async def test_index_rename_never_moves_between_owner_directories(
    integrations, tmp_path, target
):
    module = importlib.import_module("models.index_model")
    source = tmp_path / "indexes" / "42" / "original"
    source.mkdir(parents=True)
    handler = module.Index_handler(None, None)
    assert not await handler.rename_index(None, "indexes/42/original", target)
    assert source.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 429, 500])
async def test_media_provider_failures_do_not_expose_provider_body(
    integrations, monkeypatch, status
):
    module = importlib.import_module("models.index_model")
    handler = module.Index_handler(None, None)
    context = mock_context(integrations)
    monkeypatch.setattr(module, "create_index_context", lambda **kwargs: context)
    handler.index_uploaded_file = AsyncMock(
        side_effect=module.OpenAIAPIError("PRIVATE_PROVIDER_BODY_SENTINEL", status)
    )
    monkeypatch.setattr(module, "save_attachment", AsyncMock())
    ctx = SimpleNamespace(
        respond=AsyncMock(), channel=SimpleNamespace(send=AsyncMock())
    )
    file = SimpleNamespace(filename="recording.wav", content_type="audio/wav", size=10)

    await handler.set_file_index(ctx, file, user_api_key="personal-key")

    embed = ctx.channel.send.await_args.kwargs["embed"]
    rendered = str(embed.to_dict())
    assert "PRIVATE_PROVIDER_BODY_SENTINEL" not in rendered
    assert "OpenAI" in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize("current_key", [None, "new-key"])
async def test_data_chat_revokes_changed_personal_keys_before_uploads_or_requests(
    integrations, monkeypatch, current_key
):
    from tests.test_index_features import index_session
    from services.agent_service import personal_key_fingerprint

    module, cog, message = index_session(integrations, monkeypatch)
    monkeypatch.setattr(module, "USER_INPUT_API_KEYS", True)
    monkeypatch.setattr(module, "USER_KEY_DB", {42: current_key})
    cog.index_handler.index_chat_chains[5].key_fingerprint = personal_key_fingerprint(
        "old-key"
    )
    message.author.send = AsyncMock()
    message.attachments = [SimpleNamespace(filename="private.wav")]
    cog.process_indexing = AsyncMock()

    await cog.on_message(message)

    cog.process_indexing.assert_not_awaited()
    cog.index_handler.execute_index_chat_message.assert_not_awaited()
    message.author.send.assert_awaited_once()
    assert cog.thread_awaiting_responses == []


@pytest.mark.asyncio
async def test_data_chat_can_end_with_revoked_key_and_attachments(
    integrations, monkeypatch
):
    from tests.test_index_features import index_session

    module, cog, message = index_session(integrations, monkeypatch)
    monkeypatch.setattr(module, "USER_INPUT_API_KEYS", True)
    monkeypatch.setattr(module, "USER_KEY_DB", {})
    message.content = "end"
    message.attachments = [SimpleNamespace(filename="private.wav")]
    cog.process_indexing = AsyncMock()

    await cog.on_message(message)

    cog.process_indexing.assert_not_awaited()
    cog.index_handler.execute_index_chat_message.assert_awaited_once_with(
        message, "end"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("bypass", [False, True])
async def test_github_reader_honors_proxy_and_no_proxy_without_token_in_url(
    integrations, monkeypatch, bypass
):
    module = importlib.import_module("models.index_model")
    github = pytest.importorskip("llama_index.readers.github")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:8080")
    monkeypatch.setenv("https_proxy", "http://proxy.invalid:8080")
    monkeypatch.setenv("NO_PROXY", "api.github.com" if bypass else "")
    monkeypatch.setenv("no_proxy", "api.github.com" if bypass else "")
    monkeypatch.setattr(
        module.EnvService, "get_github_token", lambda: "private-github-token"
    )
    monkeypatch.setattr(
        module.requests,
        "get",
        lambda *args, **kwargs: SimpleNamespace(
            status_code=200, json=lambda: {"default_branch": "develop"}
        ),
    )
    calls = []
    actual_client = module.httpx.AsyncClient

    class Client(actual_client):
        def __init__(self, **kwargs):
            calls.append(kwargs)
            super().__init__(**kwargs)

        async def request(self, method, url, **kwargs):
            request = self.build_request(method, url, **kwargs)
            assert request.headers["Authorization"] == "Bearer private-github-token"
            assert "private-github-token" not in str(request.url)
            assert (
                str(request.url)
                == "https://api.github.com/repos/owner/project/git/trees/develop"
            )
            return module.httpx.Response(200, json={}, request=request)

    class Reader:
        def __init__(self, client, **kwargs):
            self.client = client

        def load_data(self, branch):
            asyncio.run(
                self.client.request(
                    "getTree", "GET", owner="owner", repo="project", tree_sha=branch
                )
            )
            return [Document(text="Proxy tested repository")]

    monkeypatch.setattr(module.httpx, "AsyncClient", Client)
    monkeypatch.setattr(github, "GithubRepositoryReader", Reader)
    handler = module.Index_handler(None, None)
    index = await asyncio.to_thread(
        handler.index_github_repository,
        "https://github.com/owner/project",
        mock_context(integrations),
    )
    assert "Proxy tested" in contents(index)
    assert calls[0]["proxy"] == (None if bypass else "http://proxy.invalid:8080")


@pytest.mark.asyncio
async def test_deep_composition_survives_an_unavailable_tokenizer_vocabulary(
    integrations, monkeypatch, tmp_path
):
    import llama_index.core
    from llama_index.core import VectorStoreIndex
    from llama_index.core.embeddings import MockEmbedding
    from llama_index.core.llms import MockLLM
    from services import usage_service

    monkeypatch.setattr(usage_service, "_tokenizer_unavailable", True)
    monkeypatch.setattr(llama_index.core, "global_tokenizer", None)
    context = integrations.create_index_context()

    class NamedLLM(MockLLM):
        model: str = "gpt-6-luna"

    context.llm = NamedLLM(max_tokens=4, callback_manager=context.callback_manager)
    context.embed_model = MockEmbedding(
        embed_dim=8, callback_manager=context.callback_manager
    )
    first = VectorStoreIndex.from_documents(
        [Document(text="First fallback source")], **context.index_kwargs
    )
    second = VectorStoreIndex.from_documents(
        [Document(text="Second fallback source")], **context.index_kwargs
    )
    module = importlib.import_module("models.index_model")
    handler = module.Index_handler(
        None,
        SimpleNamespace(
            update_usage=AsyncMock(), get_price=AsyncMock(return_value=0.01)
        ),
    )
    monkeypatch.setattr(module.EnvService, "find_shared_file", lambda name: name)
    monkeypatch.setattr(
        handler,
        "index_load_file",
        lambda path, ctx: first if str(path).endswith("first") else second,
    )

    price = await handler.compose_indexes(
        42, ["first", "second"], "fallback", True, context
    )

    assert price > 0
    assert callable(llama_index.core.global_tokenizer)
    assert isinstance(
        llama_index.core.global_tokenizer.__self__, integrations.ByteTokenizer
    )
    assert (tmp_path / "indexes" / "42" / "fallback" / "docstore.json").exists()


@pytest.mark.asyncio
async def test_reused_media_context_uses_thread_safe_sync_embedding_client(
    integrations, tmp_path
):
    import httpx
    from openai import OpenAI, AsyncOpenAI

    module = importlib.import_module("models.index_model")
    context = mock_context(integrations)
    requests = []

    def embed(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer personal-key"
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [{"object": "embedding", "index": 0, "embedding": [0.1] * 8}],
                "model": "text-embedding-3-small",
                "usage": {"prompt_tokens": 5, "total_tokens": 5},
            },
        )

    async def forbidden_async_embedding(request):
        raise AssertionError(
            "Worker indexing must not retain a client on temporary event loops"
        )

    sync_client = OpenAI(
        api_key="personal-key",
        http_client=httpx.Client(transport=httpx.MockTransport(embed)),
    )
    async_client = AsyncOpenAI(
        api_key="personal-key",
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(forbidden_async_embedding)
        ),
    )
    embedding = integrations.IndexEmbedding(
        model="text-embedding-3-small",
        api_key="personal-key",
        dimensions=8,
        callback_manager=context.callback_manager,
    )
    embedding._client = sync_client
    embedding._aclient = async_client
    context.embed_model = embedding
    model = SimpleNamespace(
        send_transcription_request=AsyncMock(
            return_value="The recording codename is FENNEL."
        )
    )
    handler = module.Index_handler(None, None, model=model)
    source = tmp_path / "source.wav"
    source.write_bytes(b"Simulated audio")
    try:
        first = await handler.index_uploaded_file(source, context, ".wav", "first.wav")
        second = await handler.index_uploaded_file(
            source, context, ".wav", "second.wav"
        )
        assert "FENNEL" in contents(first)
        assert "FENNEL" in contents(second)
        assert len(requests) == 2
        assert sync_client.is_closed() is False
    finally:
        sync_client.close()
        await async_client.close()


@pytest.mark.asyncio
async def test_multilevel_deep_composition_keeps_complete_summary_strings(
    integrations, monkeypatch
):
    from llama_index.core import VectorStoreIndex
    from llama_index.core.llms import MockLLM

    module = importlib.import_module("models.index_model")
    context = mock_context(integrations)
    summaries = []

    class SyncSummaryLLM(MockLLM):
        model: str = "gpt-6-luna"

        def predict(self, prompt, **kwargs):
            summaries.append(kwargs["context_str"])
            return "Complete summary includes FENNEL."

        async def apredict(self, *args, **kwargs):
            raise AssertionError(
                "Worker tree construction must use synchronous SDK requests"
            )

    context.llm = SyncSummaryLLM(
        max_tokens=4, callback_manager=context.callback_manager
    )
    original = VectorStoreIndex.from_documents(
        [Document(text=f"Source document {n}") for n in range(121)],
        **context.index_kwargs,
    )
    handler = module.Index_handler(
        None,
        SimpleNamespace(
            update_usage=AsyncMock(), get_price=AsyncMock(return_value=0.01)
        ),
    )
    monkeypatch.setattr(module.EnvService, "find_shared_file", lambda name: name)
    monkeypatch.setattr(handler, "index_load_file", lambda *args: original)

    await handler.compose_indexes(42, ["source"], "multilevel", True, context)

    tree = handler.index_storage[42].queryable_index
    assert len(summaries) >= 15
    summary_nodes = [
        node.text
        for node in tree.docstore.docs.values()
        if node.text.startswith("Complete")
    ]
    assert len(summary_nodes) == len(summaries)
    assert set(summary_nodes) == {"Complete summary includes FENNEL."}


@pytest.mark.asyncio
async def test_data_chat_end_uses_authorized_cacheless_discord_thread(
    integrations, recovery, monkeypatch
):
    module = importlib.import_module("models.index_model")
    assert recovery.bot.get_guild(recovery.guild.id) is None
    fetch = AsyncMock(
        side_effect=AssertionError(
            "A recovered thread must not be fetched through the empty Gateway cache"
        )
    )
    monkeypatch.setattr(recovery.bot, "fetch_channel", fetch)
    edit = AsyncMock(return_value=thread_payload(archived=True))
    monkeypatch.setattr(recovery.state.http, "edit_channel", edit)
    handler = module.Index_handler(recovery.bot, None)
    handler.index_chat_chains[recovery.thread.id] = SimpleNamespace(
        owner_id=recovery.message().author.id
    )
    message = SimpleNamespace(channel=recovery.thread, reply=AsyncMock())

    result = await handler.execute_index_chat_message(message, "end")

    assert result == "Ended chat session."
    assert recovery.thread.id not in handler.index_chat_chains
    message.reply.assert_awaited_once_with("Ending chat session.")
    edit.assert_awaited_once_with(
        recovery.thread.id, name="Closed-GPT", archived=True, reason=None
    )
    fetch.assert_not_awaited()
