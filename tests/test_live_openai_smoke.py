"""Offline checks for live-smoke consent, redaction, and request budgets."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests import live_openai_smoke as smoke


def test_usage_output_excludes_provider_strings_and_unrecognized_fields():
    assert smoke.numeric_usage(
        {
            "total_tokens": 17,
            "provider_text": "untrusted value",
            "input_tokens": "untrusted value",
            "completion_tokens_details": {"reasoning_tokens": 5, "unexpected": "value"},
        }
    ) == {"total_tokens": 17, "completion_tokens_details": {"reasoning_tokens": 5}}


@pytest.mark.parametrize(
    "status,code,category",
    [
        (401, "invalid_api_key", "authentication_or_permission"),
        (429, "insufficient_quota", "quota"),
        (429, None, "rate_limit"),
        (404, "model_not_found", "model_or_endpoint_unavailable"),
    ],
)
def test_failures_do_not_serialize_exception_content(status, code, category):
    class ProviderFailure(Exception):
        def __str__(self):
            pytest.fail("Never format provider errors in a smoke report")

    error = ProviderFailure("sensitive provider detail")
    error.status_code = status
    error.code = code
    result = smoke.failure_shape(error)
    assert result == {"category": category, "http_status": status}
    assert "sensitive" not in json.dumps(result)


@pytest.mark.asyncio
async def test_responses_wrapper_enforces_single_call_and_small_output():
    create = AsyncMock(return_value=object())
    bounded = smoke.BoundedResponses(
        SimpleNamespace(responses=SimpleNamespace(create=create))
    )
    await bounded.create(
        model="test-model", input="Test", max_tool_calls=3, max_output_tokens=4096
    )
    assert create.await_args.kwargs == {
        "model": "test-model",
        "input": "Test",
        "max_tool_calls": 1,
        "max_output_tokens": smoke.OUTPUT_TOKEN_LIMIT,
    }


def test_cli_requires_explicit_live_opt_in():
    with pytest.raises(SystemExit) as exit_info:
        smoke.main([])
    assert exit_info.value.code == 2


def test_cli_rejects_unknown_features_before_credential_lookup():
    with pytest.raises(SystemExit) as exit_info:
        smoke.main(["--run-live", "--features", "not-a-feature"])
    assert exit_info.value.code == 2


@pytest.mark.asyncio
async def test_quota_stops_remaining_features_without_another_call(tmp_path):
    class QuotaFailure(Exception):
        status = 429
        code = "insufficient_quota"

    model = SimpleNamespace(
        send_request=AsyncMock(side_effect=QuotaFailure("private error text")),
        send_edit_request=AsyncMock(),
    )
    usage = SimpleNamespace(update_usage_from_response=AsyncMock())
    emitted = []
    result = await smoke._run_checks(
        model,
        usage,
        tmp_path,
        ["chat", "edit", "image"],
        tmp_path / "artifacts",
        emitted.append,
    )
    assert [check["status"] for check in result["checks"]] == [
        "failed",
        "skipped",
        "skipped",
    ]
    assert result["checks"][0]["category"] == "quota"
    assert result["checks"][1]["reason"] == "blocked_by_quota"
    model.send_request.assert_awaited_once()
    model.send_edit_request.assert_not_awaited()
    assert len(emitted) == 3
    assert "private error text" not in json.dumps(result)


async def test_language_and_key_validation_exercise_production_helpers_once(
    tmp_path, monkeypatch
):
    from models.openai_model import Model
    from services.environment_service import EnvService

    validate = AsyncMock(
        return_value={
            "choices": [{"message": {"content": None}}],
            "usage": {"total_tokens": 4},
        }
    )
    monkeypatch.setattr(Model, "send_test_request", validate)
    monkeypatch.setattr(
        EnvService, "get_openai_token", lambda: "offline-validation-key"
    )
    model = SimpleNamespace(
        send_language_detect_request=AsyncMock(
            return_value={"choices": [{"message": {"content": "French"}}]}
        )
    )
    result = await smoke._run_checks(
        model,
        SimpleNamespace(update_usage_from_response=AsyncMock()),
        tmp_path,
        ["validate", "language"],
        tmp_path / "artifacts",
        lambda outcome: None,
    )
    assert [item["status"] for item in result["checks"]] == ["passed", "passed"]
    validate.assert_awaited_once_with("offline-validation-key")
    model.send_language_detect_request.assert_awaited_once()
    assert result["checks"][0]["usage"] == [{"total_tokens": 4}]
    assert result["checks"][1]["shape"]["expected_language"] is True
    assert "offline-validation-key" not in json.dumps(result)


@pytest.mark.parametrize(
    "action,known,unknown",
    [(None, 0, 1), ({"type": "search"}, 1, 0), ({"type": "open_page"}, 0, 0)],
)
def test_web_report_distinguishes_tool_invocations_from_known_search_actions(
    action, known, unknown
):
    from services.responses_service import parse_search_response

    tool = {"type": "web_search_call", "status": "completed"}
    if action is not None:
        tool["action"] = action
    result = parse_search_response(
        {
            "status": "completed",
            "model": "test-model",
            "output": [
                tool,
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Harmless fixture answer.",
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url": "https://www.python.org/",
                                    "title": "Python",
                                }
                            ],
                        }
                    ],
                },
            ],
        }
    )
    shape = smoke.web_result_shape(result)
    assert shape == {
        "content_chars": len("Harmless fixture answer."),
        "citations": 1,
        "web_tool_calls": 1,
        "known_search_actions": known,
        "unknown_search_actions": unknown,
    }


def test_web_report_does_not_accept_citations_without_tool_execution():
    result = SimpleNamespace(
        text="Answer",
        citations=[object()],
        web_tool_calls=0,
        search_calls=0,
        unknown_search_actions=0,
    )
    with pytest.raises(AssertionError):
        smoke.web_result_shape(result)


@pytest.mark.asyncio
async def test_imported_bot_is_rejected_before_storage_or_environment_changes(
    monkeypatch,
):
    from unittest.mock import Mock

    monkeypatch.setitem(smoke.sys.modules, "models.openai_model", object())
    create_storage = Mock(side_effect=AssertionError("Storage must remain untouched"))
    monkeypatch.setattr(smoke.tempfile, "mkdtemp", create_storage)
    with pytest.raises(smoke.SmokeIsolationError):
        await smoke.run_live(["chat"])
    create_storage.assert_not_called()
    assert smoke.failure_shape(smoke.SmokeIsolationError()) == {
        "category": "requires_fresh_process"
    }


def test_sdk_timeout_has_specific_safe_category():
    from openai import APITimeoutError

    error = APITimeoutError(request=SimpleNamespace())
    assert smoke.failure_shape(error) == {"category": "timeout"}
