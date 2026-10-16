"""Discord CDN downloads honor egress without forwarding API credentials."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import discord
import pytest

from services import discord_attachment_service as attachments


@pytest.fixture
def cdn(monkeypatch):
    calls = []
    options = []
    response = SimpleNamespace(
        status=200, reason="OK", read=AsyncMock(return_value=b"attachment bytes")
    )

    class ResponseContext:
        async def __aenter__(self):
            return response

        async def __aexit__(self, *args):
            return False

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return ResponseContext()

    def make_session(**kwargs):
        options.append(kwargs)
        return Session()

    monkeypatch.setattr(attachments.aiohttp, "ClientSession", make_session)
    monkeypatch.setattr(
        attachments.EnvService, "get_http_proxy", lambda _: "http://proxy.test:8080"
    )
    return SimpleNamespace(calls=calls, options=options, response=response)


def attachment():
    return SimpleNamespace(
        url="https://cdn.discordapp.com/attachments/example/file.wav?ex=signed",
        read=AsyncMock(return_value=b"direct attachment bytes"),
    )


@pytest.mark.asyncio
async def test_proxy_download_preserves_bytes_without_api_or_netrc_credentials(
    cdn, monkeypatch
):
    monkeypatch.setenv("OPENAI_API_KEY", "private-openai-key")
    monkeypatch.setenv("DISCORD_TOKEN", "private-discord-token")
    file = attachment()

    assert await attachments.read_attachment_bytes(file) == b"attachment bytes"

    file.read.assert_not_awaited()
    assert cdn.calls == [(file.url, {"proxy": "http://proxy.test:8080"})]
    assert cdn.options[0]["trust_env"] is False
    assert cdn.options[0]["timeout"].total == 300
    assert "auth" not in cdn.options[0]
    assert "headers" not in cdn.options[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("bypass", [False, True], ids=["no-proxy", "no-proxy-rule"])
async def test_direct_and_no_proxy_downloads_keep_pycord_behavior(monkeypatch, bypass):
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.test:8080" if bypass else "")
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setenv("NO_PROXY", "cdn.discordapp.com" if bypass else "")
    file = attachment()

    assert await attachments.read_attachment_bytes(file) == b"direct attachment bytes"

    file.read.assert_awaited_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, expected",
    [(403, discord.Forbidden), (404, discord.NotFound), (500, discord.HTTPException)],
)
async def test_proxy_http_failure_preserves_discord_errors(cdn, status, expected):
    cdn.response.status = status
    file = attachment()

    with pytest.raises(expected) as failure:
        await attachments.read_attachment_bytes(file)

    assert failure.value.status == status
    file.read.assert_not_awaited()
    cdn.response.read.assert_not_awaited()
    assert len(cdn.calls) == 1


@pytest.mark.asyncio
async def test_proxy_connection_failure_never_retries_with_direct_egress(cdn):
    cdn.response.read.side_effect = aiohttp.ClientConnectionError("Connection failed")
    file = attachment()

    with pytest.raises(aiohttp.ClientConnectionError):
        await attachments.read_attachment_bytes(file)

    file.read.assert_not_awaited()
    assert len(cdn.calls) == 1


@pytest.mark.asyncio
async def test_save_attachment_uses_the_same_proxy_download(cdn, tmp_path):
    file = attachment()
    path = tmp_path / "attachment.wav"

    written = await attachments.save_attachment(file, path)

    assert path.read_bytes() == b"attachment bytes"
    assert written == len(b"attachment bytes")
    assert len(cdn.calls) == 1
    file.read.assert_not_awaited()
