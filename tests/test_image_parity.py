"""Current Image API source-guided variations and their Discord controls."""

import asyncio
import base64
from copy import deepcopy
import importlib
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from PIL import Image
import pytest

from models.openai_model import Models, OpenAIAPIError
from tests.test_conversation_flow import MEMBER, PARENT, conversation  # noqa: F401
from tests.test_image_controls import click, current_view, images, retry  # noqa: F401
from tests.test_openai_model import fake_api, model  # noqa: F401
from tests.test_prompt_optimizer import optimizer_flow  # noqa: F401


def png_bytes(color="green"):
    content = io.BytesIO()
    with Image.new("RGB", (16, 16), color) as image:
        image.save(content, format="PNG")
    return content.getvalue()


def saved_source(model):
    source = Path(model.IMAGE_SAVE_PATH) / "generated-source.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(png_bytes())
    return source


@pytest.mark.asyncio
@pytest.mark.parametrize("response_type", ["base64", "url"])
async def test_variation_uses_documented_json_edit_and_saves_each_source(
    model, fake_api, monkeypatch, response_type
):
    source = saved_source(model)
    output = png_bytes("red")
    result = (
        {"b64_json": base64.b64encode(output).decode()}
        if response_type == "base64"
        else {"url": "https://image.invalid/generated.png"}
    )
    if response_type == "url":
        requests = importlib.import_module("models.openai_model").requests
        monkeypatch.setattr(
            requests,
            "get",
            lambda *args, **kwargs: SimpleNamespace(
                content=output, raise_for_status=lambda: None
            ),
        )
    usage = {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}
    fake_api.queued.append(({"data": [result], "usage": usage}, 200))
    model.num_images = 1

    attachment, paths = await model.send_image_edit_request(
        SimpleNamespace(guild=None),
        "An emerald dragon",
        str(source),
        "low",
        "1024x1024",
        "natural",
        custom_api_key="fresh-personal-key",
    )

    try:
        url, options = fake_api.calls[0]
        assert url == "https://example.invalid/v1/images/edits"
        assert options["headers"]["Authorization"] == "Bearer fresh-personal-key"
        payload = options["json"]
        assert payload["model"] == Models.IMAGE
        assert payload["images"] == [
            {
                "image_url": "data:image/png;base64,"
                + base64.b64encode(source.read_bytes()).decode()
            }
        ]
        assert "Preserve its subject" in payload["prompt"]
        assert "Original prompt: An emerald dragon" in payload["prompt"]
        assert payload["quality"] == "low"
        assert payload["size"] == "1024x1024"
        assert payload["n"] == 1
        assert payload["output_format"] == "png"
        assert "style" not in payload
        assert source.read_bytes() == png_bytes()
        assert len(paths) == 1 and Path(paths[0]) != source
        with Image.open(paths[0]) as saved:
            assert saved.convert("RGB").getpixel((0, 0)) == (255, 0, 0)
        model.usage_service.update_usage_from_response.assert_awaited_once_with(
            usage, Models.IMAGE
        )
    finally:
        attachment.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["outside", "symlink", "missing", "format", "size"])
async def test_variations_reject_unavailable_or_unowned_sources_before_api(
    model, fake_api, tmp_path, invalid
):
    source = saved_source(model)
    if invalid == "outside":
        source = tmp_path / "unowned.png"
        source.write_bytes(png_bytes())
    elif invalid == "symlink":
        outside = tmp_path / "unowned.png"
        outside.write_bytes(png_bytes())
        source.unlink()
        source.symlink_to(outside)
    elif invalid == "missing":
        source.unlink()
    elif invalid == "format":
        source.write_text("not an image")
    elif invalid == "size":
        with source.open("ab") as output:
            output.truncate(15_000_001)

    with pytest.raises(ValueError):
        await model.send_image_edit_request(None, "A dragon", str(source))

    assert fake_api.calls == []


@pytest.mark.asyncio
async def test_edit_api_failure_is_typed_and_leaves_source_unchanged(model, fake_api):
    source = saved_source(model)
    fake_api.queued.append(
        ({"error": {"message": "Invalid source", "code": "invalid_image"}}, 400)
    )

    with pytest.raises(OpenAIAPIError) as error:
        await model.send_image_edit_request(None, "A dragon", str(source))

    assert error.value.status == 400
    assert len(fake_api.calls) == 1
    assert source.read_bytes() == png_bytes()


@pytest.fixture
async def variations(images):
    flow = images
    generate = flow.image_model.send_image_request.side_effect

    async def edit(ctx, prompt, source, quality, size, style, **kwargs):
        assert source in [str(path) for path, _ in flow.generated]
        return await generate(ctx, prompt, quality, size, style, **kwargs)

    flow.image_model.send_image_edit_request = AsyncMock(side_effect=edit)
    return flow


def vary(view):
    return next(item for item in view.children if item.label == "Vary 1")


@pytest.mark.asyncio
async def test_vary_adds_new_image_and_retry_retains_original_source_with_fresh_key(
    variations,
):
    flow = variations
    original = await flow.publish_image()
    view = current_view(flow, original)
    unchanged = deepcopy(flow.messages[original.id])
    source = str(flow.generated[0][0])
    flow.image_model.send_image_request.reset_mock()
    flow.image_configuration.USER_KEY_DB[MEMBER] = "rotated-variation-key"
    existing_messages = set(flow.messages)

    await vary(view).callback(click(flow, original))

    edit = flow.image_model.send_image_edit_request
    assert edit.await_args.args[1:] == (
        view.request.prompt,
        source,
        "low",
        "1024x1024",
        "natural",
    )
    assert edit.await_args.kwargs == {"custom_api_key": "rotated-variation-key"}
    new_messages = set(flow.messages) - existing_messages
    assert len(new_messages) == 1
    variant_view = current_view(flow, SimpleNamespace(id=new_messages.pop()))
    request = variant_view.request
    variant = request.response
    assert variant.id != original.id
    assert request.image_reference == source
    assert flow.messages[original.id] == unchanged
    assert len(variant.attachments) == 1
    assert variant_view.lock is view.lock
    flow.image_configuration.USER_KEY_DB[MEMBER] = "rotated-again-key"

    await retry(variant_view).callback(click(flow, variant))

    assert edit.await_count == 2
    assert edit.await_args.args[2] == source
    assert edit.await_args.kwargs == {"custom_api_key": "rotated-again-key"}
    assert len(flow.messages[variant.id]["attachments"]) == 1
    assert flow.messages[original.id] == unchanged
    flow.image_model.send_image_request.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["owner", "message", "channel"])
async def test_variation_denies_unrelated_owner_message_or_channel_before_key_lookup(
    variations, monkeypatch, mismatch
):
    flow = variations
    message = await flow.publish_image()
    view = current_view(flow, message)
    text = importlib.import_module("services.text_service")
    lookup = AsyncMock(side_effect=AssertionError("Denied request read API key"))
    monkeypatch.setattr(text.TextService, "get_user_api_key", lookup)
    interaction = click(
        flow,
        message,
        user=MEMBER + 1 if mismatch == "owner" else MEMBER,
        channel=PARENT + 1 if mismatch == "channel" else None,
    )
    if mismatch == "message":
        interaction.message.id += 1

    await vary(view).callback(interaction)

    assert flow.adapter.initial[interaction.id]["data"]["flags"] & 64
    lookup.assert_not_awaited()
    flow.image_model.send_image_edit_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_removed_personal_key_opens_setup_without_varying(variations):
    flow = variations
    message = await flow.publish_image()
    view = current_view(flow, message)
    flow.image_configuration.USER_KEY_DB.clear()
    interaction = click(flow, message)

    await vary(view).callback(interaction)

    response = flow.adapter.initial[interaction.id]
    assert response["type"] == 9
    assert response["data"]["title"] == "API Key Setup"
    flow.image_model.send_image_edit_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_variation_moderation_runs_after_ack_before_paid_request(
    variations, monkeypatch
):
    flow = variations
    message = await flow.publish_image()
    view = current_view(flow, message)
    monkeypatch.setattr(flow.image_configuration, "PRE_MODERATE", True)
    moderation = importlib.import_module("services.moderations_service")

    async def blocked(prompt, ctx):
        assert ctx.interaction.response.is_done()
        return True

    moderate = AsyncMock(side_effect=blocked)
    monkeypatch.setattr(moderation.Moderation, "simple_moderate_and_respond", moderate)

    await vary(view).callback(click(flow, message))

    moderate.assert_awaited_once()
    flow.image_model.send_image_edit_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_variation_shares_retry_lock_and_recovers_after_failure(variations):
    flow = variations
    message = await flow.publish_image()
    view = current_view(flow, message)
    unchanged = deepcopy(flow.messages[message.id])
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(*args, **kwargs):
        entered.set()
        await release.wait()
        raise OpenAIAPIError("private provider detail", 400, "invalid_image")

    edit = flow.image_model.send_image_edit_request
    edit.side_effect = blocked
    pending = asyncio.create_task(vary(view).callback(click(flow, message)))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        competing = click(flow, message)
        await retry(view).callback(competing)
        response = flow.adapter.initial[competing.id]
        assert response["data"]["flags"] & 64
        assert "wait" in response["data"]["content"].lower()
    finally:
        release.set()
        await asyncio.wait_for(pending, 1)

    edit.assert_awaited_once()
    assert not view.lock.locked()
    assert flow.messages[message.id] == unchanged
    error_message = flow.adapter.followups[-1][1]
    assert error_message["flags"] & 64
    assert "private provider detail" not in error_message["content"]


@pytest.mark.asyncio
async def test_varying_variant_uses_its_own_output_and_timeout_keeps_download(
    variations,
):
    flow = variations
    message = await flow.publish_image()
    existing_messages = set(flow.messages)
    await vary(current_view(flow, message)).callback(click(flow, message))
    new_messages = set(flow.messages) - existing_messages
    assert len(new_messages) == 1
    variant_view = current_view(flow, SimpleNamespace(id=new_messages.pop()))
    variant = variant_view.message
    variant_source = str(flow.generated[1][0])

    await vary(variant_view).callback(click(flow, variant))

    assert flow.image_model.send_image_edit_request.await_args.args[2] == variant_source
    await variant_view.on_timeout()
    labels = [
        item["label"]
        for row in flow.messages[variant.id]["components"]
        for item in row["components"]
    ]
    assert labels == ["Save 1"]
