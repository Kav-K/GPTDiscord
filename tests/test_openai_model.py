"""Offline contracts for current OpenAI payloads and Discord image delivery."""

import base64
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from models import openai_model
from models.openai_model import Model, Models, OpenAIAPIError


@pytest.fixture
def model(monkeypatch, tmp_path):
    monkeypatch.setattr(openai_model, "SETTINGS_DB", {})
    monkeypatch.setenv("IMAGE_SAVE_PATH", str(tmp_path / "images"))
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_IMAGE_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_MODERATION_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "bot-test-key")
    monkeypatch.setenv("OPENAI_PROJECT", "project-test")
    monkeypatch.setenv("OPENAI_ORGANIZATION", "org-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    service = SimpleNamespace(update_usage_from_response=AsyncMock())
    return Model(service)


@pytest.fixture
def fake_api(monkeypatch):
    """Replace the network boundary, preserving async context-manager semantics."""
    queued, calls, session_options = [], [], []

    class Response:
        def __init__(self, payload, status):
            self.payload, self.status = payload, status

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def json(self):
            if isinstance(self.payload, Exception):
                raise self.payload
            return self.payload

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            assert queued, "Unexpected API request"
            payload, status = queued.pop(0)
            return Response(payload, status)

    def session_factory(**kwargs):
        session_options.append(kwargs)
        return Session()

    monkeypatch.setattr(openai_model.aiohttp, "ClientSession", session_factory)
    return SimpleNamespace(queued=queued, calls=calls, session_options=session_options)


def completion(content="OK"):
    return {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
    }


@pytest.mark.asyncio
async def test_defaults_keep_completion_budget_separate_from_context(model, fake_api):
    fake_api.queued.append((completion(), 200))
    await model.send_request("Hello", None)
    payload = fake_api.calls[0][1]["json"]
    assert payload["model"] == Models.DEFAULT == "gpt-6.1-sol"
    assert Models.LOW_USAGE_MODEL == "gpt-6-luna"
    assert Models.get_max_tokens(Models.DEFAULT) == 1_050_000
    assert payload["max_completion_tokens"] == 4000
    assert model.max_tokens < Models.get_max_tokens(model.model)
    model.usage_service.update_usage_from_response.assert_awaited_once_with(
        completion()["usage"], Models.DEFAULT
    )


@pytest.mark.parametrize(
    "selected",
    [
        Models.GPT_6_1_SOL,
        Models.GPT_6_LUNA,
        Models.GPT_5_5,
        Models.GPT_5_MINI,
        Models.O3,
        Models.O4_MINI,
    ],
)
@pytest.mark.asyncio
async def test_reasoning_payload_omits_incompatible_sampling_and_stop(
    model, fake_api, selected
):
    fake_api.queued.append((completion(), 200))
    await model.send_request(
        "Hello",
        25,
        model=selected,
        system_instruction="Be concise",
        temp_override=0,
        frequency_penalty_override=1,
        stop="END",
        max_tokens_override=1200,
    )
    payload = fake_api.calls[0][1]["json"]
    assert payload["max_completion_tokens"] == 1200
    assert payload["messages"] == [
        {"role": "developer", "content": "Be concise"},
        {"role": "user", "content": "Hello"},
    ]
    for field in (
        "temperature",
        "top_p",
        "presence_penalty",
        "frequency_penalty",
        "stop",
        "max_tokens",
    ):
        assert field not in payload


@pytest.mark.asyncio
async def test_non_reasoning_honors_zero_overrides_and_context_budget(model, fake_api):
    fake_api.queued.append((completion(), 200))
    await model.send_request(
        "Hello",
        Models.get_max_tokens(Models.GPT_4_1_MINI) - 120,
        model=Models.GPT_4_1_MINI,
        temp_override=0,
        top_p_override=0,
        frequency_penalty_override=0,
        presence_penalty_override=0,
        max_tokens_override=900,
    )
    payload = fake_api.calls[0][1]["json"]
    assert payload["max_completion_tokens"] == 120
    for field in ("temperature", "top_p", "frequency_penalty", "presence_penalty"):
        assert payload[field] == 0
    assert "stop" not in payload


@pytest.mark.asyncio
async def test_oversized_prompt_fails_before_network(model, fake_api):
    with pytest.raises(ValueError, match="context window"):
        await model.send_request("Hello", Models.get_max_tokens(model.model))
    assert fake_api.calls == []


@pytest.mark.asyncio
async def test_invalid_credentials_are_not_retried_or_billed(model, fake_api):
    fake_api.queued.append(({"error": {"message": "Invalid API key"}}, 401))
    with pytest.raises(OpenAIAPIError, match="Invalid API key"):
        await model.send_request("Hello", None)
    assert len(fake_api.calls) == 1
    model.usage_service.update_usage_from_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_transient_api_failure_is_retried(model, fake_api, monkeypatch):
    import backoff._async

    monkeypatch.setattr(backoff._async.asyncio, "sleep", AsyncMock())
    fake_api.queued.extend(
        [({"error": {"message": "Rate limited"}}, 429), (completion(), 200)]
    )
    assert await model.send_request("Hello", None) == completion()
    assert len(fake_api.calls) == 2
    model.usage_service.update_usage_from_response.assert_awaited_once()


@pytest.mark.asyncio
async def test_custom_key_does_not_inherit_host_project(model, fake_api):
    fake_api.queued.extend([(completion(), 200), (completion(), 200)])
    await model.send_request("Hello", None)
    assert fake_api.calls[0][0] == "https://example.invalid/v1/chat/completions"
    assert fake_api.calls[0][1]["headers"]["OpenAI-Project"] == "project-test"
    assert fake_api.calls[0][1]["headers"]["OpenAI-Organization"] == "org-test"
    await model.send_request("Hello", None, custom_api_key="user-test-key")
    assert fake_api.calls[1][1]["headers"] == {"Authorization": "Bearer user-test-key"}


@pytest.mark.asyncio
async def test_modern_vision_includes_first_and_later_images(model, fake_api):
    fake_api.queued.append((completion(), 200))
    history = [
        SimpleNamespace(
            text="First image", image_urls=["https://example.invalid/1.png"]
        ),
        SimpleNamespace(
            text="\nAnother User: Second image",
            image_urls=["https://example.invalid/2.png"],
        ),
    ]
    await model.send_chatgpt_chat_request(
        history,
        Models.DEFAULT,
        "GPTie",
        "A name ✨",
        system_prompt_override="Describe the images",
    )
    messages = fake_api.calls[0][1]["json"]["messages"]
    assert messages[0]["role"] == "developer"
    assert messages[1]["name"] == "A_name__"
    assert messages[1]["content"][1]["image_url"]["url"].endswith("1.png")
    assert messages[2]["content"][1]["image_url"]["url"].endswith("2.png")


@pytest.mark.asyncio
async def test_scoped_conversation_instruction_preserves_opener_and_history(
    model, fake_api
):
    history = [
        SimpleNamespace(text="You are a helpful assistant.", image_urls=None),
        SimpleNamespace(text="\nUser: My question", image_urls=None),
        SimpleNamespace(text="\nGPTie: Prior answer", image_urls=None),
        SimpleNamespace(text="\nUser: Follow up", image_urls=None),
    ]
    original = [item.text for item in history]
    fake_api.queued.append((completion(), 200))

    await model.send_chatgpt_chat_request(
        history,
        Models.DEFAULT,
        "GPTie",
        "User",
        system_instruction="Answer in Spanish.",
    )

    messages = fake_api.calls[0][1]["json"]["messages"]
    assert messages[:2] == [
        {"role": "developer", "content": original[0]},
        {"role": "developer", "content": "Answer in Spanish."},
    ]
    assert [message["role"] for message in messages[2:]] == [
        "user",
        "assistant",
        "user",
    ]
    assert [item.text for item in history] == original


@pytest.mark.asyncio
async def test_json_request_uses_api_json_mode(model, fake_api):
    fake_api.queued.append((completion('{"intent_to_draw": true}'), 200))
    result = await model.send_chatgpt_chat_request(
        [SimpleNamespace(text="Draw a cat", image_urls=None)],
        Models.LOW_USAGE_MODEL,
        "GPTie",
        "User",
        system_prompt_override="Classify",
        respond_json=True,
    )
    assert result == {"intent_to_draw": True}
    payload = fake_api.calls[0][1]["json"]
    assert payload["response_format"] == {"type": "json_object"}
    assert any("JSON" in str(message["content"]) for message in payload["messages"])


@pytest.mark.asyncio
async def test_embedding_and_moderation_use_current_models(model, fake_api):
    usage = {"prompt_tokens": 4, "total_tokens": 4}
    fake_api.queued.extend(
        [
            ({"data": [{"embedding": [0.1, 0.2]}], "usage": usage}, 200),
            ({"results": []}, 200),
        ]
    )
    assert await model.send_embedding_request("Text") == [0.1, 0.2]
    assert fake_api.calls[0][1]["json"]["model"] == "text-embedding-3-small"
    assert fake_api.calls[0][1]["json"]["dimensions"] == 1536
    model.usage_service.update_usage_from_response.assert_awaited_once_with(
        usage, Models.EMBEDDINGS
    )
    await model.send_moderations_request("Text")
    assert fake_api.calls[1][1]["json"]["model"] == "omni-moderation-latest"


@pytest.mark.asyncio
async def test_image_generation_uses_base64_and_current_parameters(model, fake_api):
    usage = {"input_tokens": 10, "output_tokens": 40, "total_tokens": 50}
    fake_api.queued.append(({"data": [{"b64_json": "aW1hZ2U="}], "usage": usage}, 200))
    references = await model.send_image_request_within_conversation(
        "A cat", "hd", "1792x1024", "vivid", num_images=2
    )
    payload = fake_api.calls[0][1]["json"]
    assert payload["model"] == "gpt-image-2.5-flare"
    assert payload["quality"] == "high"
    assert payload["size"] == "1536x1024"
    assert payload["output_format"] == "png"
    assert payload["n"] == 2
    assert "style" not in payload and "response_format" not in payload
    assert references == ["data:image/png;base64,aW1hZ2U="]
    model.usage_service.update_usage_from_response.assert_awaited_once_with(
        usage, Models.IMAGE
    )


@pytest.mark.asyncio
async def test_base64_images_are_saved_and_combined_without_downloads(
    model, monkeypatch
):
    source = io.BytesIO()
    Image.new("RGB", (20, 10), "red").save(source, format="PNG")
    reference = "data:image/png;base64," + base64.b64encode(source.getvalue()).decode()
    monkeypatch.setattr(
        openai_model.requests,
        "get",
        lambda *args, **kwargs: pytest.fail("Unexpected image download"),
    )
    attachment, paths = await model.save_image_urls_and_return(
        [reference, reference], SimpleNamespace(guild=None)
    )
    try:
        assert len(paths) == 2
        with Image.open(paths[0]) as saved:
            assert saved.size == (20, 10)
        with Image.open(attachment.fp) as combined:
            assert combined.size == (40, 10)
        assert attachment.filename == "generated-images.png"
    finally:
        attachment.close()


@pytest.mark.asyncio
async def test_retired_variations_fail_without_network(model, fake_api):
    with pytest.raises(ValueError, match="retired"):
        await model.send_image_request_old(None, "A cat", vary="old.png")
    assert fake_api.calls == []


def test_selecting_large_model_preserves_user_output_budget(model):
    model.max_tokens = 1200
    model.model = Models.GPT_5_4
    assert model.max_tokens == 1200
    model.low_usage_mode = True
    assert model.low_usage_mode is True
    assert model.model == Models.LOW_USAGE_MODEL
    model.low_usage_mode = "false"
    assert model.low_usage_mode is False
    assert model.model == Models.DEFAULT


@pytest.mark.asyncio
async def test_discord_transcription_uses_proxy_aware_attachment_read(
    model, fake_api, monkeypatch
):
    from unittest.mock import Mock

    file = Mock(spec=openai_model.discord.Attachment)
    file.filename = "voice.ogg"
    file.content_type = "audio/ogg"
    file.read = AsyncMock()
    read_attachment = AsyncMock(return_value=b"test audio")
    monkeypatch.setattr(openai_model, "read_attachment_bytes", read_attachment)
    fake_api.queued.append(({"text": "Hello"}, 200))
    assert (
        await model.send_transcription_request(file, temperature_override=0) == "Hello"
    )
    read_attachment.assert_awaited_once_with(file)
    file.read.assert_not_awaited()
    assert fake_api.calls[0][0].endswith("/audio/transcriptions")
    fields = {
        field[0]["name"]: field[2] for field in fake_api.calls[0][1]["data"]._fields
    }
    assert fields["file"] == b"test audio"
    assert fields["temperature"] == "0"


@pytest.mark.asyncio
async def test_reasoning_budget_exhaustion_is_reported_and_usage_recorded(
    model, fake_api
):
    response = completion(None)
    response["choices"][0]["finish_reason"] = "length"
    fake_api.queued.append((response, 200))
    with pytest.raises(ValueError, match="Increase max_tokens"):
        await model.send_request("Hello", None)
    model.usage_service.update_usage_from_response.assert_awaited_once()


@pytest.mark.asyncio
async def test_insufficient_quota_is_not_retried(model, fake_api):
    fake_api.queued.append(
        ({"error": {"message": "Quota exhausted", "code": "insufficient_quota"}}, 429)
    )
    with pytest.raises(OpenAIAPIError, match="Quota exhausted"):
        await model.send_request("Hello", None)
    assert len(fake_api.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("request_kind", ["chat", "key_validation", "transcription"])
@pytest.mark.parametrize(
    "payload, expected",
    [
        (ValueError("invalid JSON"), "non-JSON response"),
        ([], "invalid JSON response"),
        ({"error": "Upstream rejected request"}, "Upstream rejected request"),
    ],
)
async def test_all_api_paths_report_invalid_responses_consistently(
    model, fake_api, request_kind, payload, expected
):
    fake_api.queued.append((payload, 400))
    audio = openai_model.discord.File(io.BytesIO(b"audio"), filename="voice.wav")
    try:
        with pytest.raises(OpenAIAPIError, match=expected) as failure:
            if request_kind == "chat":
                await model.send_request("Hello", None)
            elif request_kind == "key_validation":
                await model.send_test_request("user-test-key")
            else:
                await model.send_transcription_request(audio)
        assert failure.value.status == 400
        assert len(fake_api.calls) == 1
        model.usage_service.update_usage_from_response.assert_not_awaited()
    finally:
        audio.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [{}, {"text": None}, {"text": ["bad"]}])
async def test_transcription_requires_text_response(model, fake_api, response):
    fake_api.queued.append((response, 200))
    audio = openai_model.discord.File(io.BytesIO(b"audio"), filename="voice.wav")
    try:
        with pytest.raises(ValueError, match="invalid transcription"):
            await model.send_transcription_request(audio)
    finally:
        audio.close()


@pytest.mark.asyncio
async def test_all_api_sessions_honor_environment_proxies(model, fake_api, monkeypatch):
    import discord
    from unittest.mock import Mock

    choose_proxy = Mock(return_value="http://proxy.test:8080")
    monkeypatch.setattr(openai_model.EnvService, "get_http_proxy", choose_proxy)

    fake_api.queued.extend(
        [(completion(), 200), (completion(), 200), ({"text": "Hello"}, 200)]
    )
    await model.send_request("Hello", None)
    await model.send_test_request("user-test-key")
    audio = discord.File(io.BytesIO(b"audio"), filename="voice.wav")
    try:
        await model.send_transcription_request(audio)
    finally:
        audio.close()
    assert len(fake_api.session_options) == 3
    assert all(
        not options.get("trust_env", False) for options in fake_api.session_options
    )
    assert all(call[1]["proxy"] == "http://proxy.test:8080" for call in fake_api.calls)
    assert [call.args[0] for call in choose_proxy.call_args_list] == [
        call[0] for call in fake_api.calls
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("request_kind", ["chat", "key_validation", "transcription"])
async def test_target_netrc_credentials_never_conflict_with_bearer_auth(
    model, monkeypatch, tmp_path, request_kind
):
    """Exercise real aiohttp auth/proxy processing, stopping before any socket."""
    import aiohttp
    import discord

    netrc = tmp_path / "test.netrc"
    netrc.write_text(
        "machine example.invalid login offline-netrc-user password offline-netrc-password\n"
    )
    netrc.chmod(0o600)
    monkeypatch.setenv("NETRC", str(netrc))
    proxy = "http://proxy.invalid:8080"
    monkeypatch.setattr(openai_model.EnvService, "get_http_proxy", lambda url: proxy)
    observed = []

    class RequestReachedTransport(Exception):
        pass

    async def intercept_connection(connector, request, **kwargs):
        observed.append(request)
        raise RequestReachedTransport()

    monkeypatch.setattr(aiohttp.TCPConnector, "connect", intercept_connection)

    # Confirm the fixture reproduces the external library's credential conflict.
    async with aiohttp.ClientSession(trust_env=True) as session:
        with pytest.raises(ValueError, match="AUTHORIZATION"):
            await session.post(
                "https://example.invalid/v1/chat/completions",
                headers={"Authorization": "Bearer offline-test-key"},
                proxy=proxy,
            )
    assert observed == []

    with pytest.raises(RequestReachedTransport):
        if request_kind == "chat":
            await model.send_request("Hello", None)
        elif request_kind == "key_validation":
            await model.send_test_request("offline-test-key")
        else:
            audio = discord.File(io.BytesIO(b"audio"), filename="voice.wav")
            try:
                await model.send_transcription_request(audio)
            finally:
                audio.close()
    assert len(observed) == 1
    assert observed[0].headers["Authorization"].startswith("Bearer ")
    assert str(observed[0].proxy) == proxy
