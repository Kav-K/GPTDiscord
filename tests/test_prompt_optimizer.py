"""Optimizer controls keep their own prompts and acknowledge real SDK interactions."""

import asyncio
from copy import deepcopy
import importlib
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from discord.webhook.async_ import async_context
from PIL import Image
import pytest

from models.openai_model import Models, OpenAIAPIError
from tests.test_conversation_controls import (
    CONTROL_IDS,
    StrictInteractionAdapter,
    component_interaction,
)
from tests.test_conversation_flow import (
    BOT,
    GUILD,
    MEMBER,
    PARENT,
    conversation,  # noqa: F401
    member_payload,
)


class OptimizerAdapter(StrictInteractionAdapter):
    def __init__(self, flow):
        super().__init__()
        self.flow = flow
        self.uploads = []

    async def execute_webhook(self, identifier, token, *, payload, **kwargs):
        if payload is None:
            payload = json.loads(
                next(
                    part["value"]
                    for part in kwargs["multipart"]
                    if part["name"] == "payload_json"
                )
            )
        data = await super().execute_webhook(
            identifier, token, payload=payload, **kwargs
        )
        for field in ("components", "embeds"):
            if field in payload:
                data[field] = deepcopy(payload[field])
        for file in kwargs.get("files") or []:
            content = file.fp.read()
            self.uploads.append((file.filename, content))
            data["attachments"].append(
                {
                    "id": str(int(data["id"]) + len(data["attachments"]) + 1),
                    "filename": file.filename,
                    "size": len(content),
                    "content_type": "image/png",
                    "url": "https://cdn.invalid/generated-image.png",
                    "proxy_url": "https://proxy.invalid/generated-image.png",
                }
            )
        self.flow.messages[int(data["id"])] = deepcopy(data)
        return data

    async def edit_webhook_message(
        self, identifier, token, message_id, *, payload, **kwargs
    ):
        data = deepcopy(self.flow.messages[message_id])
        data.update(deepcopy(payload))
        self.flow.messages[message_id] = deepcopy(data)
        self.flow.edits.append(deepcopy(data))
        return data


@pytest.fixture
async def optimizer_flow(conversation, monkeypatch):
    flow = conversation
    module = importlib.import_module("cogs.prompt_optimizer_cog")
    monkeypatch.setattr(module, "USER_INPUT_API_KEYS", True)
    monkeypatch.setattr(module, "USER_KEY_DB", {MEMBER: "original-personal-key"})
    monkeypatch.setattr(module, "PRE_MODERATE", False)
    adapter = OptimizerAdapter(flow)
    context_token = async_context.set(adapter)
    flow.adapter = adapter
    flow.optimizer_module = module
    flow.expected_interaction = None
    flow.optimized_text = "Optimized Prompt: A detailed blue circle on white."

    async def generate(*args, **kwargs):
        assert flow.expected_interaction.response.is_done()
        assert adapter.initial[flow.expected_interaction.id]["type"] in (5, 6)
        return {"choices": [{"message": {"content": flow.optimized_text}}]}

    flow.optimize_model = SimpleNamespace(send_request=AsyncMock(side_effect=generate))
    flow.image_cog = SimpleNamespace(bot=flow.bot)
    flow.optimizer = module.ImgPromptOptimizer(
        flow.bot,
        flow.usage,
        flow.optimize_model,
        asyncio.Queue(),
        asyncio.Queue(),
        flow.cog,
        flow.image_cog,
    )
    flow.image_send = AsyncMock()
    flow.original_image_send = module.ImageService.encapsulated_send
    monkeypatch.setattr(module.ImageService, "encapsulated_send", flow.image_send)
    monkeypatch.setattr(
        module.TextService,
        "encapsulated_send",
        AsyncMock(
            side_effect=AssertionError("Optimizer entered generic chat handling")
        ),
    )
    try:
        yield flow
    finally:
        async_context.reset(context_token)


def slash_context(flow):
    identifier = next(CONTROL_IDS)
    interaction = discord.Interaction(
        state=flow.state,
        data={
            "id": str(identifier),
            "type": 2,
            "application_id": str(BOT),
            "token": f"offline-component-{identifier}-{PARENT}",
            "version": 1,
            "guild_id": str(GUILD),
            "channel_id": str(PARENT),
            "member": member_payload(),
            "data": {"id": "1800000000000000000", "name": "optimize", "type": 1},
        },
    )
    flow.expected_interaction = interaction
    return discord.ApplicationContext(flow.bot, interaction)


def published_view(flow, message):
    views = [
        view
        for view, _ in flow.state._view_store._views.values()
        if isinstance(view, flow.optimizer_module.OptimizeView)
        and view.message_id == message.id
    ]
    assert views, "Published optimizer message must have a bound view"
    return views[0]


def button(view, label):
    return next(item for item in view.children if item.label == label)


def click(flow, message, label, *, user_id=MEMBER):
    interaction = component_interaction(flow, message, user_id=user_id)
    interaction.data["custom_id"] = button(
        published_view(flow, message), label
    ).custom_id
    flow.expected_interaction = interaction
    return interaction


async def publish(flow, prompt="Draw a blue circle"):
    ctx = slash_context(flow)
    message = await flow.optimizer.optimize_command(ctx, prompt)
    assert message is not None
    return message


def private_denial(flow, interaction):
    response = flow.adapter.initial[interaction.id]
    assert response["type"] == 4
    assert response["data"]["flags"] & 64
    return response["data"]["content"]


@pytest.mark.asyncio
async def test_optimizer_publishes_real_followup_with_own_binding_and_personal_key(
    optimizer_flow,
):
    flow = optimizer_flow
    message = await publish(flow)

    assert isinstance(message, discord.WebhookMessage)
    assert message.id != flow.expected_interaction.id
    assert message.content == "A detailed blue circle on white."
    assert flow.adapter.initial[flow.expected_interaction.id]["type"] == 5
    view = published_view(flow, message)
    assert view.owner_id == MEMBER
    assert view.prompt == "Draw a blue circle"
    assert getattr(view, "custom_api_key", None) is None
    call = flow.optimize_model.send_request.await_args
    assert call.kwargs["model"] == Models.LOW_USAGE_MODEL
    assert call.kwargs["max_tokens_override"] == 2048
    assert call.kwargs["custom_api_key"] == "original-personal-key"
    flow.image_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_preserves_active_chat_and_uses_original_prompt_with_current_key(
    optimizer_flow,
):
    flow = optimizer_flow
    history = [object(), object()]
    conversation = SimpleNamespace(history=history)
    redo = object()
    flow.cog.conversation_threads[PARENT] = conversation
    flow.cog.redo_users[MEMBER] = redo
    flow.cog.users_to_interactions[MEMBER] = [123]
    message = await publish(flow, "The original raw prompt")
    view = published_view(flow, message)
    flow.optimizer_module.USER_KEY_DB[MEMBER] = "rotated-personal-key"
    flow.optimized_text = "Output Prompt: The regenerated optimized prompt."
    interaction = click(flow, message, "Retry")

    await button(view, "Retry").callback(interaction)

    assert flow.adapter.initial[interaction.id]["type"] == 6
    assert flow.messages[message.id]["content"] == "The regenerated optimized prompt."
    request = flow.optimize_model.send_request.await_args
    assert "The original raw prompt" in request.args[0]
    assert request.kwargs["model"] == Models.LOW_USAGE_MODEL
    assert request.kwargs["custom_api_key"] == "rotated-personal-key"
    assert flow.cog.conversation_threads[PARENT] is conversation
    assert conversation.history is history
    assert flow.cog.redo_users[MEMBER] is redo
    assert flow.cog.users_to_interactions[MEMBER] == [123]
    flow.optimizer_module.TextService.encapsulated_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_draw_acknowledges_before_normal_image_workflow_with_current_key(
    optimizer_flow,
):
    flow = optimizer_flow
    message = await publish(flow)
    view = published_view(flow, message)
    flow.optimizer_module.USER_KEY_DB[MEMBER] = "rotated-image-key"
    interaction = click(flow, message, "Draw")

    async def generate(*args, **kwargs):
        assert interaction.response.is_done()
        assert flow.adapter.initial[interaction.id]["type"] == 5

    flow.image_send.side_effect = generate
    await button(view, "Draw").callback(interaction)

    flow.image_send.assert_awaited_once()
    request = flow.image_send.await_args
    assert request.args[:3] == (flow.image_cog, MEMBER, message.content)
    assert isinstance(request.args[3], discord.ApplicationContext)
    assert request.args[3].interaction is interaction
    assert request.kwargs == {
        "custom_api_key": "rotated-image-key",
        "dalle_3": True,
        "image_size": "1024x1024",
        "style": "vivid",
        "quality": "auto",
    }


@pytest.mark.asyncio
async def test_draw_delivers_real_image_followup_and_complete_image_retry_metadata(
    optimizer_flow, monkeypatch, tmp_path
):
    flow = optimizer_flow
    message = await publish(flow)
    view = published_view(flow, message)
    interaction = click(flow, message, "Draw")
    flow.optimizer_module.USER_KEY_DB[MEMBER] = "current-image-key"
    source = tmp_path / "generated-source.png"
    with Image.new("RGB", (8, 8), "blue") as image:
        image.save(source, format="PNG")
    image_bytes = source.read_bytes()
    attachment = discord.File(io.BytesIO(image_bytes), filename="generated-images.png")
    flow.image_cog.converser_cog = flow.cog
    flow.image_cog.redo_users = {}

    async def generate(ctx, prompt, quality, image_size, style, **kwargs):
        assert ctx.interaction is interaction
        assert interaction.response.is_done()
        assert flow.adapter.initial[interaction.id]["type"] == 5
        return attachment, [str(source)]

    flow.image_cog.model = SimpleNamespace(
        send_image_request=AsyncMock(side_effect=generate)
    )
    monkeypatch.setattr(
        flow.optimizer_module.ImageService,
        "encapsulated_send",
        flow.original_image_send,
    )
    fetch = AsyncMock(
        side_effect=lambda channel, identifier: deepcopy(flow.messages[identifier])
    )
    monkeypatch.setattr(flow.state.http, "get_message", fetch)
    try:
        await button(view, "Draw").callback(interaction)

        flow.image_cog.model.send_image_request.assert_awaited_once()
        request = flow.image_cog.model.send_image_request.await_args
        assert request.args[1:] == (message.content, "auto", "1024x1024", "vivid")
        assert request.kwargs == {"custom_api_key": "current-image-key"}
        assert flow.adapter.uploads == [("generated-images.png", image_bytes)]
        redo = flow.image_cog.redo_users[MEMBER]
        assert isinstance(redo.response, discord.Message)
        assert redo.response.id != interaction.id
        assert redo.prompt == message.content
        assert redo.dalle_3 is True
        assert redo.quality == "auto"
        assert redo.image_size == "1024x1024"
        assert redo.style == "vivid"
        assert len(redo.response.attachments) == 1
        assert redo.response.attachments[0].filename == "generated-images.png"
        fetch.assert_not_awaited()
        assert redo.response.id in flow.cog.users_to_interactions[MEMBER]
        image_module = importlib.import_module("services.image_service")
        image_views = [
            stored_view
            for stored_view, _ in flow.state._view_store._views.values()
            if isinstance(stored_view, image_module.SaveView)
            and stored_view.message.id == redo.response.id
        ]
        assert image_views
        assert image_views[0].dalle_3 is True
        assert getattr(image_views[0], "custom_api_key", None) is None
        assert flow.messages[redo.response.id]["components"]
    finally:
        attachment.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["slash", "Retry", "Draw"])
async def test_missing_current_key_opens_modal_before_any_paid_work(
    optimizer_flow, operation
):
    flow = optimizer_flow
    message = await publish(flow) if operation != "slash" else None
    flow.optimize_model.send_request.reset_mock()
    flow.optimizer_module.USER_KEY_DB.clear()
    if operation == "slash":
        ctx = slash_context(flow)
        interaction = ctx.interaction
        await flow.optimizer.optimize_command(ctx, "A raw prompt")
    else:
        view = published_view(flow, message)
        interaction = click(flow, message, operation)
        await button(view, operation).callback(interaction)

    response = flow.adapter.initial[interaction.id]
    assert response["type"] == 9
    assert response["data"]["title"] == "API Key Setup"
    flow.optimize_model.send_request.assert_not_awaited()
    flow.image_send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("label", ["Retry", "Draw"])
@pytest.mark.parametrize("mismatch", ["owner", "message"])
async def test_unrelated_users_or_messages_cannot_use_optimizer_credentials(
    optimizer_flow, monkeypatch, label, mismatch
):
    flow = optimizer_flow
    message = await publish(flow)
    view = published_view(flow, message)
    flow.optimize_model.send_request.reset_mock()
    lookup = AsyncMock(side_effect=AssertionError("Ownership must precede key lookup"))
    monkeypatch.setattr(flow.optimizer_module.TextService, "get_user_api_key", lookup)
    interaction = click(
        flow, message, label, user_id=MEMBER + 1 if mismatch == "owner" else MEMBER
    )
    if mismatch == "message":
        interaction.message.id += 1

    await button(view, label).callback(interaction)

    assert "yourself" in private_denial(flow, interaction)
    lookup.assert_not_awaited()
    flow.optimize_model.send_request.assert_not_awaited()
    flow.image_send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["slash", "Retry", "Draw"])
async def test_moderation_stops_optimizer_and_draw_after_acknowledgement(
    optimizer_flow, monkeypatch, operation
):
    flow = optimizer_flow
    message = await publish(flow) if operation != "slash" else None
    flow.optimize_model.send_request.reset_mock()
    monkeypatch.setattr(flow.optimizer_module, "PRE_MODERATE", True)

    async def moderate(prompt, ctx):
        assert ctx.interaction.response.is_done()
        await ctx.respond("Blocked by offline moderation.", ephemeral=True)
        return True

    moderation = AsyncMock(side_effect=moderate)
    monkeypatch.setattr(
        flow.optimizer_module.Moderation, "simple_moderate_and_respond", moderation
    )
    if operation == "slash":
        ctx = slash_context(flow)
        await flow.optimizer.optimize_command(ctx, "The raw prompt")
    else:
        view = published_view(flow, message)
        interaction = click(flow, message, operation)
        await button(view, operation).callback(interaction)

    moderation.assert_awaited_once()
    flow.optimize_model.send_request.assert_not_awaited()
    flow.image_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_draw_and_retry_do_not_issue_two_paid_requests(optimizer_flow):
    flow = optimizer_flow
    message = await publish(flow)
    view = published_view(flow, message)
    flow.optimize_model.send_request.reset_mock()
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked_image(*args, **kwargs):
        entered.set()
        await release.wait()

    flow.image_send.side_effect = blocked_image
    first = click(flow, message, "Draw")
    task = asyncio.create_task(button(view, "Draw").callback(first))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        second = click(flow, message, "Retry")
        await button(view, "Retry").callback(second)
        assert "wait" in private_denial(flow, second).lower()
        flow.optimize_model.send_request.assert_not_awaited()
    finally:
        release.set()
        await asyncio.wait_for(task, 1)
    flow.image_send.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["slash", "Retry"])
@pytest.mark.parametrize("failure", ["provider", "unexpected"])
async def test_optimizer_errors_do_not_echo_credentials_or_provider_details(
    optimizer_flow, operation, failure
):
    flow = optimizer_flow
    message = await publish(flow) if operation == "Retry" else None
    secret = "private-key-and-account-detail"
    flow.optimize_model.send_request.side_effect = (
        OpenAIAPIError(secret, 401) if failure == "provider" else RuntimeError(secret)
    )
    if operation == "slash":
        ctx = slash_context(flow)
        await flow.optimizer.optimize_command(ctx, "A raw prompt")
    else:
        view = published_view(flow, message)
        interaction = click(flow, message, "Retry")
        await button(view, "Retry").callback(interaction)

    assert flow.adapter.followups
    _, payload = flow.adapter.followups[-1]
    assert payload["flags"] & 64
    assert secret not in payload["content"]
    assert payload["content"].strip()


@pytest.mark.asyncio
async def test_image_errors_are_sanitized_after_draw_acknowledgement(optimizer_flow):
    flow = optimizer_flow
    message = await publish(flow)
    view = published_view(flow, message)
    secret = "private-image-provider-detail"
    flow.image_send.side_effect = RuntimeError(secret)
    interaction = click(flow, message, "Draw")

    await button(view, "Draw").callback(interaction)

    assert flow.adapter.initial[interaction.id]["type"] == 5
    _, payload = flow.adapter.followups[-1]
    assert payload["flags"] & 64
    assert secret not in payload["content"]
