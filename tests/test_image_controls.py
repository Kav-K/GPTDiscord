"""Image controls bind to real delivered messages and current personal keys."""

import asyncio
from copy import deepcopy
import importlib
import io
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from discord.webhook.async_ import async_context
from PIL import Image
import pytest

from tests.test_conversation_controls import component_interaction
from tests.test_conversation_flow import (
    GUILD,
    MEMBER,
    PARENT,
    conversation,
)  # noqa: F401
from tests.test_prompt_optimizer import (
    OptimizerAdapter,
    optimizer_flow,  # noqa: F401
    slash_context,
)


@pytest.fixture
async def images(optimizer_flow, monkeypatch, tmp_path):
    flow = optimizer_flow
    module = importlib.import_module("services.image_service")
    configuration = importlib.import_module("cogs.image_service_cog")
    monkeypatch.setattr(configuration, "USER_INPUT_API_KEYS", True)
    monkeypatch.setattr(configuration, "USER_KEY_DB", {MEMBER: "original-image-key"})
    monkeypatch.setattr(configuration, "PRE_MODERATE", False)
    monkeypatch.setattr(
        module.ImageService, "encapsulated_send", flow.original_image_send
    )
    adapter = OptimizerAdapter(flow)
    context = async_context.set(adapter)
    flow.adapter = adapter
    flow.image_module = module
    flow.image_configuration = configuration
    flow.image_cog.converser_cog = flow.cog
    flow.image_cog.redo_users = {}
    flow.generated = []
    flow.generated_files = []

    async def generate(ctx, prompt, quality, size, style, **kwargs):
        assert ctx.interaction.response.is_done()
        identifier = len(flow.generated)
        source = tmp_path / f"source-{identifier}.png"
        with Image.new("RGB", (8, 8), (identifier % 256, 40, 180)) as image:
            image.save(source, format="PNG")
        content = source.read_bytes()
        file = discord.File(io.BytesIO(content), filename="generated-images.png")
        flow.generated.append((source, content))
        flow.generated_files.append(file)
        return file, [str(source)]

    flow.image_model = SimpleNamespace(
        send_image_request=AsyncMock(side_effect=generate)
    )
    flow.image_cog.model = flow.image_model

    async def edit_files(channel, identifier, files, **fields):
        assert channel == PARENT
        data = deepcopy(flow.messages[identifier])
        retained = deepcopy(fields.get("attachments", data["attachments"]))
        for file in files:
            content = file.fp.read()
            adapter.uploads.append((file.filename, content))
            retained.append(
                {
                    "id": str(identifier + len(adapter.uploads)),
                    "filename": file.filename,
                    "size": len(content),
                    "content_type": "image/png",
                    "url": "https://cdn.invalid/regenerated.png",
                    "proxy_url": "https://proxy.invalid/regenerated.png",
                }
            )
        data.update(deepcopy(fields))
        data["attachments"] = retained
        flow.messages[identifier] = deepcopy(data)
        flow.edits.append(deepcopy(data))
        return data

    flow.edit_files = AsyncMock(side_effect=edit_files)
    monkeypatch.setattr(flow.state.http, "edit_files", flow.edit_files)
    flow.fetch_image = AsyncMock(
        side_effect=AssertionError("Delivered images need no fetch")
    )
    monkeypatch.setattr(flow.state.http, "get_message", flow.fetch_image)

    async def publish(prompt="The image's own original prompt"):
        ctx = slash_context(flow)
        await ctx.defer(invisible=False)
        return await module.ImageService.encapsulated_send(
            flow.image_cog,
            MEMBER,
            prompt,
            ctx,
            custom_api_key="original-image-key",
            dalle_3=True,
            quality="low",
            image_size="1024x1024",
            style="natural",
        )

    flow.publish_image = publish
    try:
        yield flow
    finally:
        for file in flow.generated_files:
            file.close()
        async_context.reset(context)


def current_view(flow, message):
    request = flow.image_cog.redo_users[MEMBER]
    for view, _ in flow.state._view_store._views.values():
        if (
            isinstance(view, flow.image_module.SaveView)
            and view.message.id == message.id
            and view.request is request
        ):
            return view
    pytest.fail("The delivered image must have its own registered controls")


def retry(view):
    return next(item for item in view.children if item.label == "Retry")


def click(flow, message, *, user=MEMBER, channel=None):
    return component_interaction(flow, message, user_id=user, channel_id=channel)


@pytest.mark.asyncio
async def test_initial_delivery_binds_bot_authenticated_message_and_image_metadata(
    images,
):
    flow = images
    message = await flow.publish_image()

    assert type(message) is discord.Message
    assert len(message.attachments) == 1
    assert flow.adapter.uploads == [("generated-images.png", flow.generated[0][1])]
    view = current_view(flow, message)
    assert view.owner_id == MEMBER
    assert view.request.response.id == message.id
    assert view.request.prompt == "The image's own original prompt"
    assert view.request.dalle_3 is True
    assert view.request.quality == "low"
    assert view.request.image_size == "1024x1024"
    assert view.request.style == "natural"
    assert getattr(view, "custom_api_key", None) is None
    flow.fetch_image.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_reads_current_key_and_replaces_original_attachment_once(images):
    flow = images
    message = await flow.publish_image()
    view = current_view(flow, message)
    flow.image_configuration.USER_KEY_DB[MEMBER] = "rotated-image-key"
    flow.image_model.send_image_request.reset_mock()
    interaction = click(flow, message)

    await retry(view).callback(interaction)

    assert flow.adapter.initial[interaction.id]["type"] == 6
    flow.image_model.send_image_request.assert_awaited_once()
    request = flow.image_model.send_image_request.await_args
    assert request.args[1:] == (view.request.prompt, "low", "1024x1024", "natural")
    assert request.kwargs == {"custom_api_key": "rotated-image-key"}
    flow.edit_files.assert_awaited_once()
    assert flow.edit_files.await_args.args[:2] == (PARENT, message.id)
    assert flow.edit_files.await_args.kwargs["attachments"] == []
    assert len(flow.messages[message.id]["attachments"]) == 1
    assert flow.adapter.uploads[-1][1] == flow.generated[-1][1]
    assert flow.image_cog.redo_users[MEMBER].response.id == message.id
    assert current_view(flow, message).lock is view.lock


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["owner", "message", "channel"])
async def test_retry_rejects_unrelated_identity_or_target_before_key_lookup(
    images, monkeypatch, mismatch
):
    flow = images
    message = await flow.publish_image()
    view = current_view(flow, message)
    text = importlib.import_module("services.text_service")
    lookup = AsyncMock(side_effect=AssertionError("Denied request reached key lookup"))
    monkeypatch.setattr(text.TextService, "get_user_api_key", lookup)
    flow.image_model.send_image_request.reset_mock()
    interaction = click(
        flow,
        message,
        user=MEMBER + 1 if mismatch == "owner" else MEMBER,
        channel=PARENT + 1 if mismatch == "channel" else None,
    )
    if mismatch == "message":
        interaction.message.id += 1

    await retry(view).callback(interaction)

    response = flow.adapter.initial[interaction.id]
    assert response["type"] == 4
    assert response["data"]["flags"] & 64
    lookup.assert_not_awaited()
    flow.image_model.send_image_request.assert_not_awaited()
    flow.edit_files.assert_not_awaited()


@pytest.mark.asyncio
async def test_old_image_view_never_retries_another_images_current_global_request(
    images,
):
    flow = images
    first = await flow.publish_image("The first image prompt")
    first_view = current_view(flow, first)
    second = await flow.publish_image("A different image prompt")
    flow.image_model.send_image_request.reset_mock()

    await retry(first_view).callback(click(flow, first))

    flow.image_model.send_image_request.assert_awaited_once()
    assert (
        flow.image_model.send_image_request.await_args.args[1]
        == "The first image prompt"
    )
    assert flow.edit_files.await_args.args[1] == first.id
    assert (
        flow.messages[second.id]["embeds"][0]["description"]
        == "A different image prompt"
    )


@pytest.mark.asyncio
async def test_deleted_personal_key_opens_modal_without_regenerating(images):
    flow = images
    message = await flow.publish_image()
    view = current_view(flow, message)
    flow.image_configuration.USER_KEY_DB.clear()
    flow.image_model.send_image_request.reset_mock()
    interaction = click(flow, message)

    await retry(view).callback(interaction)

    response = flow.adapter.initial[interaction.id]
    assert response["type"] == 9
    assert response["data"]["title"] == "API Key Setup"
    flow.image_model.send_image_request.assert_not_awaited()
    flow.edit_files.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_moderation_runs_after_ack_and_before_regeneration(
    images, monkeypatch
):
    flow = images
    message = await flow.publish_image()
    view = current_view(flow, message)
    flow.image_model.send_image_request.reset_mock()
    monkeypatch.setattr(flow.image_configuration, "PRE_MODERATE", True)
    moderation = importlib.import_module("services.moderations_service")

    async def blocked(prompt, ctx):
        assert ctx.interaction.response.is_done()
        await ctx.respond("Blocked by offline moderation.", ephemeral=True)
        return True

    moderate = AsyncMock(side_effect=blocked)
    monkeypatch.setattr(moderation.Moderation, "simple_moderate_and_respond", moderate)
    interaction = click(flow, message)

    await retry(view).callback(interaction)

    moderate.assert_awaited_once()
    assert moderate.await_args.args[0] == view.request.prompt
    flow.image_model.send_image_request.assert_not_awaited()
    flow.edit_files.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_retries_issue_only_one_image_generation(images):
    flow = images
    message = await flow.publish_image()
    view = current_view(flow, message)
    generate = flow.image_model.send_image_request.side_effect
    flow.image_model.send_image_request.reset_mock()
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(*args, **kwargs):
        entered.set()
        await release.wait()
        return await generate(*args, **kwargs)

    flow.image_model.send_image_request.side_effect = blocked
    first = asyncio.create_task(retry(view).callback(click(flow, message)))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        second = click(flow, message)
        await retry(view).callback(second)
        response = flow.adapter.initial[second.id]
        assert response["type"] == 4
        assert response["data"]["flags"] & 64
        assert "wait" in response["data"]["content"].lower()
    finally:
        release.set()
        await asyncio.wait_for(first, 1)
    flow.image_model.send_image_request.assert_awaited_once()


@pytest.mark.asyncio
async def test_save_uploads_original_png_privately_without_temporary_copies(
    images, monkeypatch, tmp_path
):
    flow = images
    message = await flow.publish_image()
    view = current_view(flow, message)
    before = set(tmp_path.iterdir())

    def forbid_temp_copy(*args, **kwargs):
        pytest.fail("Saving an existing PNG must not leak a temporary copy")

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", forbid_temp_copy)
    save = next(item for item in view.children if item.label == "Save 1")
    interaction = click(flow, message)

    await save.callback(interaction)

    response = flow.adapter.initial[interaction.id]
    assert response["type"] == 5
    assert response["data"]["flags"] & 64
    assert flow.adapter.followups[-1][1]["flags"] & 64
    source, content = flow.generated[0]
    assert flow.adapter.uploads[-1] == (source.name, content)
    assert set(tmp_path.iterdir()) == before


@pytest.mark.asyncio
async def test_timeout_removes_only_retry_from_bound_message_without_expired_webhook(
    images, monkeypatch
):
    flow = images
    first = await flow.publish_image("First image")
    first_view = current_view(flow, first)
    second = await flow.publish_image("Second image")
    untouched = deepcopy(flow.messages[second.id])
    expired = AsyncMock(
        side_effect=AssertionError("Interaction webhook expired before view timeout")
    )
    monkeypatch.setattr(flow.adapter, "edit_webhook_message", expired)
    flow.edits.clear()

    await first_view.on_timeout()

    expired.assert_not_awaited()
    assert flow.edits
    assert {int(data["id"]) for data in flow.edits} == {first.id}
    labels = [
        item["label"]
        for row in flow.messages[first.id]["components"]
        for item in row["components"]
    ]
    assert labels == ["Save 1"]
    assert len(flow.messages[first.id]["attachments"]) == 1
    assert flow.messages[second.id] == untouched


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "saved_key", [False, True], ids=["setup-required", "saved-key"]
)
async def test_cacheless_draw_command_keeps_setup_acknowledgement_and_delivery_working(
    images, saved_key
):
    flow = images
    cog = flow.image_configuration.DrawDallEService(
        flow.bot,
        flow.usage,
        flow.image_model,
        asyncio.Queue(),
        asyncio.Queue(),
        flow.cog,
    )
    if not saved_key:
        flow.image_configuration.USER_KEY_DB.clear()
    ctx = slash_context(flow)
    flow.state._guilds.pop(GUILD)
    assert ctx.guild is None
    assert ctx.interaction.guild_id == GUILD

    await cog.draw_command(
        ctx, "A cacheless image prompt", "auto", "1024x1024", "vivid"
    )

    acknowledgement = flow.adapter.initial[ctx.interaction.id]
    if saved_key:
        assert acknowledgement["type"] == 5
        flow.image_model.send_image_request.assert_awaited_once()
        request = cog.redo_users[MEMBER]
        assert len(request.response.attachments) == 1
        assert flow.usage.get_usage_memory(str(GUILD))["image_drawn"] == 1
        assert flow.adapter.uploads == [("generated-images.png", flow.generated[0][1])]
    else:
        assert acknowledgement["type"] == 9
        assert acknowledgement["data"]["title"] == "API Key Setup"
        flow.image_model.send_image_request.assert_not_awaited()
        assert not flow.adapter.uploads
