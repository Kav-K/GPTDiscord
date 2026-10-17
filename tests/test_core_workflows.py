"""Offline contracts for the original core workflows and Discord image limits."""

import base64
import io
import random
from types import SimpleNamespace

from PIL import Image
import pytest

from models import openai_model
from models.openai_model import Models
from tests.test_openai_model import completion, fake_api, model  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,args,expected_model,expected_instruction,expected_input",
    [
        (
            "send_edit_request",
            ("Correct spelling only.", "Helo world."),
            Models.LOW_USAGE_MODEL,
            "Edit the provided text according to the instruction. Return only the edited result.",
            "Instruction: Correct spelling only.\n\nText to edit: Helo world.",
        ),
        (
            "send_summary_request",
            ("User: My favorite color is blue.",),
            Models.DEFAULT,
            "Summarize the conversation in detail. Preserve names, user preferences, relevant personal details, and unresolved requests. Do not summarize the assistant's instructions.",
            "User: My favorite color is blue.",
        ),
        (
            "send_language_detect_request",
            ("Bonjour.", "Return true if the text is English, otherwise false."),
            Models.LOW_USAGE_MODEL,
            "Return true if the text is English, otherwise false.",
            "Bonjour.",
        ),
    ],
    ids=["edit", "summary", "language-detection"],
)
async def test_core_text_workflows_preserve_instructions_personal_key_and_usage(
    model,
    fake_api,
    method,
    args,
    expected_model,
    expected_instruction,
    expected_input,
):
    response = completion("A successful offline response.")
    fake_api.queued.append((response, 200))

    result = await getattr(model, method)(*args, custom_api_key="personal-test-key")

    assert result == response
    assert len(fake_api.calls) == 1
    url, request = fake_api.calls[0]
    assert url == "https://example.invalid/v1/chat/completions"
    assert request["headers"] == {"Authorization": "Bearer personal-test-key"}
    assert request["json"]["model"] == expected_model
    assert request["json"]["messages"] == [
        {"role": "developer", "content": expected_instruction},
        {"role": "user", "content": expected_input},
    ]
    assert request["json"]["max_completion_tokens"] == model.max_tokens
    model.usage_service.update_usage_from_response.assert_awaited_once_with(
        response["usage"], expected_model
    )


@pytest.mark.asyncio
async def test_compatible_image_provider_urls_are_returned_without_early_downloads(
    model, fake_api, monkeypatch
):
    reference = "https://images.example.invalid/generated.png"
    usage = {"input_tokens": 4, "output_tokens": 12, "total_tokens": 16}
    fake_api.queued.append(({"data": [{"url": reference}], "usage": usage}, 200))
    monkeypatch.setattr(
        openai_model.requests,
        "get",
        lambda *args, **kwargs: pytest.fail("Generation must not download image URLs"),
    )

    references = await model.send_image_request_within_conversation(
        "A blue circle.",
        "low",
        "1024x1024",
        None,
        custom_api_key="personal-test-key",
        num_images=1,
    )

    assert references == [reference]
    assert len(fake_api.calls) == 1
    assert fake_api.calls[0][0] == "https://example.invalid/v1/images/generations"
    assert fake_api.calls[0][1]["headers"] == {
        "Authorization": "Bearer personal-test-key"
    }
    model.usage_service.update_usage_from_response.assert_awaited_once_with(
        usage, Models.IMAGE
    )


@pytest.mark.asyncio
async def test_contact_sheet_shrinks_to_guild_limit_without_reducing_saved_sources(
    model, monkeypatch
):
    source_image = Image.frombytes(
        "RGB", (128, 128), random.Random(0).randbytes(128 * 128 * 3)
    )
    source = io.BytesIO()
    source_image.save(source, format="PNG")
    limit = 4500
    assert len(source.getvalue()) > limit
    reference = "data:image/png;base64," + base64.b64encode(source.getvalue()).decode()
    monkeypatch.setattr(
        openai_model.requests,
        "get",
        lambda *args, **kwargs: pytest.fail("Local images must not require downloads"),
    )

    attachment, paths = await model.save_image_urls_and_return(
        [reference, reference],
        SimpleNamespace(guild=SimpleNamespace(filesize_limit=limit)),
    )
    try:
        assert len(attachment.fp.getvalue()) <= limit
        assert len(paths) == 2
        with Image.open(attachment.fp) as contact_sheet:
            assert 0 < contact_sheet.width < 256
            assert 0 < contact_sheet.height < 128
        for path in paths:
            with Image.open(path) as saved_source:
                assert saved_source.size == (128, 128)
                assert saved_source.convert("RGB").tobytes() == source_image.tobytes()
    finally:
        attachment.close()
        source_image.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [{}, {"data": []}, {"data": [{}]}])
async def test_missing_image_data_fails_before_discord_delivery(
    model, fake_api, response
):
    fake_api.queued.append((response, 200))

    with pytest.raises(ValueError, match="no image"):
        await model.send_image_request_within_conversation(
            "A blue circle.", "low", "1024x1024", None
        )

    assert len(fake_api.calls) == 1
    model.usage_service.update_usage_from_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_base64_provider_image_fails_before_discord_delivery(
    model, fake_api
):
    fake_api.queued.append(({"data": [{"b64_json": "%%%"}]}, 200))

    with pytest.raises(ValueError, match="invalid base64"):
        await model.send_image_request(
            SimpleNamespace(guild=None), "A blue circle.", "low", "1024x1024", None
        )

    assert len(fake_api.calls) == 1
