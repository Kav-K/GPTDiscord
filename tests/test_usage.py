import asyncio
from unittest.mock import Mock

import pytest
from requests import HTTPError

from services import usage_service
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


@pytest.fixture
def tokenizer_state(monkeypatch):
    monkeypatch.setattr(usage_service, "_tokenizer_unavailable", False)


@pytest.mark.parametrize(
    "failure",
    [HTTPError("Denied secret-url-and-key"), OSError("Unreadable secret-cache-path")],
)
def test_unavailable_tokenizer_counts_bytes_without_repeated_downloads(
    tokenizer_state, monkeypatch, tmp_path, caplog, failure
):
    load_encoding = Mock(side_effect=failure)
    monkeypatch.setattr(usage_service.tiktoken, "get_encoding", load_encoding)
    service = UsageService(tmp_path)

    assert service.count_tokens("hello") == 5
    assert service.count_tokens("é🐈") == len("é🐈".encode("utf-8")) == 6
    assert UsageService.count_tokens_static("漢字") == 6
    assert UsageService(tmp_path).count_tokens("another prompt") == 14
    assert service.count_tokens("") == 0

    load_encoding.assert_called_once_with("cl100k_base")
    assert len(caplog.records) == 1
    assert "conservative UTF-8 byte counts" in caplog.text
    assert "secret-" not in caplog.text


def test_static_tokenizer_failure_also_disables_instance_downloads(
    tokenizer_state, monkeypatch, tmp_path, caplog
):
    load_encoding = Mock(side_effect=HTTPError("Denied"))
    monkeypatch.setattr(usage_service.tiktoken, "get_encoding", load_encoding)

    assert UsageService.count_tokens_static("ASCII") == 5
    assert UsageService.count_tokens_static("🐈") == 4
    assert UsageService(tmp_path).count_tokens("é") == 2

    load_encoding.assert_called_once()
    assert len(caplog.records) == 1


def test_native_tokenizer_behavior_is_unchanged(
    tokenizer_state, monkeypatch, tmp_path, caplog
):
    native = Mock()
    native.encode.return_value = [12, 34]
    load_encoding = Mock(return_value=native)
    monkeypatch.setattr(usage_service.tiktoken, "get_encoding", load_encoding)
    service = UsageService(tmp_path)

    assert service.count_tokens("hello world") == 2
    assert service.count_tokens("é🐈") == 2
    load_encoding.assert_called_once_with("cl100k_base")
    assert UsageService.count_tokens_static("static prompt") == 2
    native.encode.assert_called_with("static prompt")
    assert not caplog.records

    # A successful encoding is not cached globally by UsageService: another
    # caller can use a newly configured encoding without stale shared state.
    replacement = Mock()
    replacement.encode.return_value = [56]
    monkeypatch.setattr(
        usage_service.tiktoken, "get_encoding", Mock(return_value=replacement)
    )
    assert UsageService.count_tokens_static("changed encoding") == 1


@pytest.mark.asyncio
async def test_fallback_counts_do_not_change_api_usage_accounting(
    tokenizer_state, monkeypatch, tmp_path
):
    monkeypatch.setattr(
        usage_service.tiktoken, "get_encoding", Mock(side_effect=HTTPError("Denied"))
    )
    service = UsageService(tmp_path)
    assert service.count_tokens("🐈") == 4

    cost = await service.update_usage_from_response(
        {"input_tokens": 1000, "output_tokens": 200}, "gpt-6.1-sol"
    )

    assert cost == pytest.approx(0.004)
    assert await service.get_usage() == pytest.approx(0.004)
