"""DeepL transport and component acknowledgement without live service calls."""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.test_user_api_key import StrictResponse


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key,host", [("free-key:fx", "api-free.deepl.com"), ("pro-key", "api.deepl.com")]
)
async def test_deepl_uses_matching_host_auth_header_and_json_body(
    monkeypatch, key, host, capsys
):
    module = importlib.import_module("models.deepl_model")
    monkeypatch.setenv("DEEPL_TOKEN", key)
    monkeypatch.setattr(
        module.EnvService, "get_http_proxy", lambda url: "http://proxy.invalid:8080"
    )
    calls = []

    class Response:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def json(self):
            return {
                "translations": [{"text": "Bonjour", "detected_source_language": "EN"}]
            }

    class Session:
        def __init__(self, **kwargs):
            assert kwargs["raise_for_status"] is True
            assert kwargs["timeout"].total == 60

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response()

    monkeypatch.setattr(module.aiohttp, "ClientSession", Session)
    assert await module.TranslationModel().send_translate_request(
        "Private text", "FR", None
    ) == ("Bonjour", "EN")
    assert calls == [
        (
            f"https://{host}/v2/translate",
            {
                "json": {
                    "text": ["Private text"],
                    "target_lang": "FR",
                    "formality": "default",
                },
                "headers": {"Authorization": f"DeepL-Auth-Key {key}"},
                "proxy": "http://proxy.invalid:8080",
            },
        )
    ]
    assert "Private text" not in capsys.readouterr().out


@pytest.mark.asyncio
@pytest.mark.parametrize("fails", [False, True])
async def test_translate_component_acknowledges_before_provider_and_uses_followup_errors(
    monkeypatch, fails
):
    monkeypatch.setenv("ALLOWED_GUILDS", "123456789012345678")
    module = importlib.import_module("cogs.translation_service_cog")
    response = StrictResponse()
    interaction = SimpleNamespace(
        response=response, followup=SimpleNamespace(send=AsyncMock())
    )

    async def translate(*args):
        assert response.initial_response == "defer"
        if fails:
            raise ValueError("PRIVATE upstream body")
        return "Bonjour", "EN"

    message = SimpleNamespace(content="Hello", reply=AsyncMock())
    selection = SimpleNamespace(delete=AsyncMock())
    requestor = SimpleNamespace(
        name="Person",
        discriminator="0",
        avatar=None,
        default_avatar=SimpleNamespace(url="https://example.invalid/avatar.png"),
    )
    view = module.TranslateView(
        SimpleNamespace(send_translate_request=AsyncMock(side_effect=translate)),
        message,
        selection,
        requestor,
    )
    view.language, view.language_long = "FR", "French"

    await view.button_callback.callback(interaction)

    assert response.initial_response == "defer"
    if fails:
        message.reply.assert_not_awaited()
        interaction.followup.send.assert_awaited_once()
        assert "PRIVATE" not in interaction.followup.send.await_args.args[0]
    else:
        message.reply.assert_awaited_once()
        selection.delete.assert_awaited_once()
        interaction.followup.send.assert_not_awaited()
    view.stop()


@pytest.mark.asyncio
async def test_deepl_rejected_key_is_not_retried_or_logged(monkeypatch, capsys):
    module = importlib.import_module("models.deepl_model")
    calls = []

    class Response:
        async def __aenter__(self):
            raise module.aiohttp.ClientResponseError(
                request_info=SimpleNamespace(
                    real_url="https://api.deepl.com/v2/translate"
                ),
                history=(),
                status=403,
                message="PRIVATE upstream details",
            )

        async def __aexit__(self, *args):
            return False

    class Session:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            calls.append(args)
            return Response()

    monkeypatch.setattr(module.aiohttp, "ClientSession", Session)
    with pytest.raises(module.aiohttp.ClientResponseError):
        await module.TranslationModel().send_translate_request("Text", "FR", None)
    assert len(calls) == 1
    assert "PRIVATE" not in capsys.readouterr().out
