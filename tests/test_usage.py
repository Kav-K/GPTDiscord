import asyncio
from unittest.mock import Mock

import pytest

from services.usage_service import UsageService


@pytest.fixture
def usage(tmp_path, monkeypatch):
    monkeypatch.setattr("services.usage_service.tiktoken.get_encoding", Mock())
    return UsageService(tmp_path)


@pytest.mark.asyncio
async def test_actual_input_output_and_cached_usage(usage):
    cost = await usage.update_usage_from_response(
        {
            "prompt_tokens": 1000,
            "completion_tokens": 200,
            "prompt_tokens_details": {"cached_tokens": 500},
        },
        "gpt-6.1-sol",
    )
    assert cost == pytest.approx(0.00305)
    assert await usage.get_usage() == pytest.approx(cost)


@pytest.mark.asyncio
async def test_responses_usage_and_snapshots(usage):
    cost = await usage.update_usage_from_response(
        {
            "input_tokens": 1000,
            "output_tokens": 200,
            "input_tokens_details": {"cached_tokens": 500},
        },
        "gpt-6.1-sol-2026-10-01",
    )
    assert cost == pytest.approx(0.00305)


@pytest.mark.asyncio
async def test_embeddings_are_not_charged_as_text(usage):
    cost = await usage.update_usage_from_response(
        {"prompt_tokens": 1000, "total_tokens": 1000}, "text-embedding-3-small"
    )
    assert cost == pytest.approx(0.00002)


@pytest.mark.asyncio
async def test_unknown_models_do_not_use_davinci_price(usage, caplog):
    assert await usage.get_cost_name("custom-model") == "custom-model"
    assert (
        await usage.update_usage_from_response({"total_tokens": 1000}, "custom-model")
        is None
    )
    assert await usage.get_usage() == 0
    assert "excluded" in caplog.text


@pytest.mark.asyncio
async def test_concurrent_usage_is_not_lost(usage):
    await asyncio.gather(
        *[
            usage.update_usage_from_response(
                {"input_tokens": 1000, "output_tokens": 0}, "gpt-6.1-sol"
            )
            for _ in range(20)
        ]
    )
    assert await usage.get_usage() == pytest.approx(0.04)


@pytest.mark.asyncio
async def test_long_context_pricing_tier_and_mini_are_distinct(usage):
    full = await usage.update_usage_from_response(
        {"input_tokens": 300000, "output_tokens": 1000}, "gpt-5.4"
    )
    mini = await usage.update_usage_from_response(
        {"input_tokens": 300000, "output_tokens": 1000}, "gpt-5.4-mini"
    )
    assert full == pytest.approx(1.5225)
    assert mini == pytest.approx(0.2295)


def test_all_selectable_text_models_have_prices():
    from models.openai_model import Models

    assert all(
        UsageService.token_rates(model) is not None for model in Models.TEXT_MODELS
    )


@pytest.mark.asyncio
async def test_embedding_price_guard_uses_input_rate(usage):
    assert await usage.get_price(1_000_000, "text-embedding-3-small") == pytest.approx(
        0.02
    )
    assert await usage.get_price(1_000_000, "text-embedding-3-large") == pytest.approx(
        0.13
    )
