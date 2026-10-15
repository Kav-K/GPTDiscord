import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from models.openai_model import OpenAIAPIError, Override


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 429, 500])
async def test_text_api_errors_do_not_publish_provider_credentials(status):
    module = importlib.import_module("services.text_service")
    error = OpenAIAPIError("Rejected sk-proj-secret-key for secret-org", status)
    cog = SimpleNamespace(
        usage_service=SimpleNamespace(count_tokens=Mock(return_value=1)),
        model=SimpleNamespace(send_request=AsyncMock(side_effect=error)),
        conversation_threads={},
        instructions={},
        pinecone_service=None,
        remove_awaiting=Mock(),
    )
    ctx = SimpleNamespace(
        author=SimpleNamespace(id=1, display_name="User"),
        channel=SimpleNamespace(id=2),
        reply=AsyncMock(),
    )

    await module.TextService.encapsulated_send(
        cog, 1, "question", ctx, overrides=Override(), from_ask_command=True
    )

    ctx.reply.assert_awaited_once()
    assert "sk-proj-secret-key" not in ctx.reply.call_args.args[0]
    assert "secret-org" not in ctx.reply.call_args.args[0]
    cog.remove_awaiting.assert_called_once_with(1, 2, True, False)


@pytest.mark.asyncio
async def test_image_api_errors_do_not_publish_provider_credentials():
    module = importlib.import_module("services.image_service")
    cog = SimpleNamespace(
        model=SimpleNamespace(
            send_image_request=AsyncMock(
                side_effect=OpenAIAPIError("Rejected sk-proj-secret-key", 401)
            )
        )
    )
    ctx = SimpleNamespace(channel=SimpleNamespace(send=AsyncMock()))

    await module.ImageService.encapsulated_send(cog, 1, "a cat", ctx)

    ctx.channel.send.assert_awaited_once()
    assert "sk-proj-secret-key" not in ctx.channel.send.call_args.args[0]
