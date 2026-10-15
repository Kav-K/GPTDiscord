import base64
import importlib
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.mark.asyncio
async def test_modern_vision_and_drawing_preserve_user_api_key(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr("tiktoken.get_encoding", Mock())
    text_service = importlib.import_module("services.text_service")
    from models.openai_model import Models
    from models.user_model import Thread

    service = text_service.TextService
    monkeypatch.setattr(text_service, "PRE_MODERATE", False)
    monkeypatch.setattr(service, "get_user_api_key", AsyncMock(return_value="user-key"))
    monkeypatch.setattr(service, "trigger_thinking", AsyncMock())
    monkeypatch.setattr(service, "stop_thinking", AsyncMock())
    monkeypatch.setattr(service, "encapsulated_send", AsyncMock())
    image_reference = "data:image/png;base64," + base64.b64encode(b"image").decode()
    model = SimpleNamespace(
        send_chatgpt_chat_request=AsyncMock(
            return_value={"intent_to_draw": True, "prompt": "A cat", "amount": 99}
        ),
        send_image_request_within_conversation=AsyncMock(
            return_value=[image_reference]
        ),
    )
    thread = Thread(1)
    thread.model = Models.DEFAULT
    thread.drawable = True
    thread.history = []
    message = SimpleNamespace(
        content="Draw a cat inspired by this image.",
        author=SimpleNamespace(id=2, display_name="User"),
        channel=SimpleNamespace(id=1),
        reply=AsyncMock(),
    )
    cog = SimpleNamespace(
        model=model,
        END_PROMPTS=[],
        check_conversing=Mock(return_value=True),
        check_conversation_limit=AsyncMock(return_value=False),
        mention_to_username=AsyncMock(side_effect=lambda _, content: content),
        conversation_threads={1: thread},
        awaiting_responses=[],
        awaiting_thread_responses=[],
        full_conversation_history=defaultdict(list),
        pinecone_service=None,
    )

    assert await service.process_conversation_message(
        cog,
        message,
        USER_INPUT_API_KEYS=True,
        USER_KEY_DB=None,
        files=[SimpleNamespace(url="https://example.com/input.png")],
    )

    assert thread.history[0].image_urls == ["https://example.com/input.png"]
    assert thread.history[1].image_urls == [image_reference]
    classifier_args = model.send_chatgpt_chat_request.call_args
    assert classifier_args.args[1] == Models.LOW_USAGE_MODEL
    assert classifier_args.kwargs["custom_api_key"] == "user-key"
    generation_args = model.send_image_request_within_conversation.call_args
    assert generation_args.kwargs["custom_api_key"] == "user-key"
    assert generation_args.kwargs["num_images"] == 4
    message.reply.assert_awaited_once()
    assert message.reply.call_args.kwargs["content"] is None
    files = message.reply.call_args.kwargs["files"]
    assert files[0].fp.read() == b"image"
    files[0].close()


@pytest.mark.asyncio
async def test_summary_preserves_user_key_images_and_conversation_instructions(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALLOWED_GUILDS", "123456789012345678")
    module = importlib.import_module("cogs.text_service_cog")
    from models.user_model import EmbeddedConversationItem, Thread

    thread = Thread(1)
    starter = EmbeddedConversationItem("Custom drawing and image instructions", 0)
    thread.history = [
        starter,
        EmbeddedConversationItem(
            "User: Describe this image", 7, image_urls=["image-url"]
        ),
    ]
    model = SimpleNamespace(
        send_summary_request=AsyncMock(
            return_value={"choices": [{"message": {"content": "Summary"}}]}
        )
    )
    cog = SimpleNamespace(model=model, conversation_threads={1: thread})

    await module.GPT3ComCon.summarize_conversation(
        cog,
        SimpleNamespace(channel=SimpleNamespace(id=1)),
        "Conversation prompt",
        custom_api_key="user-key",
    )

    model.send_summary_request.assert_awaited_once_with(
        "Conversation prompt", custom_api_key="user-key"
    )
    assert thread.history[0] is starter
    assert "Summary" in thread.history[1].text
    assert thread.history[2].image_urls == ["image-url"]
    assert thread.history[2].timestamp == 7
