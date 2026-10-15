"""Native search is exercised offline; no Discord or OpenAI credentials needed."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from openai.types.responses import Response
import pytest

from services.responses_service import (
    Citation,
    ResponsesError,
    ResponsesService,
    SearchResponse,
    discord_messages,
    parse_search_response,
)


def api_response():
    return {
        "id": "resp_offline",
        "object": "response",
        "created_at": 0,
        "model": "gpt-6.1-sol",
        "parallel_tool_calls": True,
        "tool_choice": "required",
        "tools": [{"type": "web_search"}],
        "output": [
            {"type": "reasoning", "id": "rs_offline", "summary": []},
            {
                "type": "web_search_call",
                "id": "ws_1",
                "status": "completed",
                "action": {"type": "search", "query": "latest version"},
            },
            {
                "type": "web_search_call",
                "id": "ws_2",
                "status": "completed",
                "action": {"type": "open_page", "url": "https://example.org/release"},
            },
            {
                "id": "msg_offline",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "The release is current. [1]",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "start_index": 24,
                                "end_index": 27,
                                "title": "Release notes",
                                "url": "https://example.org/release",
                            },
                        ],
                    }
                ],
            },
        ],
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
            "input_tokens_details": {"cached_tokens": 20, "cache_write_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 10},
        },
    }


@pytest.mark.asyncio
async def test_search_uses_native_responses_with_bounded_live_search():
    # Use the real SDK Response shape so SDK attribute parsing is covered too.
    client = SimpleNamespace(
        responses=SimpleNamespace(
            create=AsyncMock(return_value=Response.model_validate(api_response()))
        )
    )
    result = await ResponsesService(client).search("  latest version?  ", "gpt-6.1-sol")
    request = client.responses.create.call_args.kwargs
    assert request["input"] == "latest version?"
    assert request["model"] == "gpt-6.1-sol"
    assert request["tools"] == [{"type": "web_search", "external_web_access": True}]
    assert request["tool_choice"] == "required"
    assert request["max_tool_calls"] == 3
    assert request["max_output_tokens"] == 4096
    assert request["store"] is False
    assert request["reasoning"] == {"effort": "low"}
    assert result.text == "The release is current. [[1]](<https://example.org/release>)"
    assert result.citations == (
        Citation("Release notes", "https://example.org/release"),
    )
    assert result.usage["input_tokens"] == 100
    assert result.usage["input_tokens_details"]["cached_tokens"] == 20
    assert result.search_calls == 1


@pytest.mark.asyncio
async def test_non_reasoning_models_do_not_receive_reasoning_parameter():
    client = SimpleNamespace(
        responses=SimpleNamespace(create=AsyncMock(return_value=api_response()))
    )
    await ResponsesService(client).search("news", "gpt-4.1")
    assert "reasoning" not in client.responses.create.call_args.kwargs


@pytest.mark.asyncio
async def test_default_client_uses_configuration_and_closes(monkeypatch):
    from unittest.mock import Mock
    import services.responses_service as module

    client = SimpleNamespace(
        responses=SimpleNamespace(create=AsyncMock(return_value=api_response()))
    )
    context = AsyncMock()
    context.__aenter__.return_value = client
    factory = Mock(return_value=context)
    monkeypatch.setattr(module, "AsyncOpenAI", factory)
    for name, value in [
        ("get_openai_token", "offline-server-key"),
        ("get_openai_organization", "offline-organization"),
        ("get_openai_project", "offline-project"),
        ("get_openai_base_url", "https://example.test/v1"),
    ]:
        monkeypatch.setattr(module.EnvService, name, lambda value=value: value)
    await ResponsesService().search("latest version?", "gpt-6.1-sol")
    assert factory.call_args.kwargs["api_key"] == "offline-server-key"
    assert factory.call_args.kwargs["base_url"] == "https://example.test/v1"
    assert factory.call_args.kwargs["project"] == "offline-project"
    assert factory.call_args.kwargs["organization"] == "offline-organization"
    context.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_custom_key_does_not_inherit_server_project_or_organization(monkeypatch):
    from unittest.mock import Mock
    import services.responses_service as module

    client = SimpleNamespace(
        responses=SimpleNamespace(create=AsyncMock(return_value=api_response())),
        organization="inherited-org",
        project="inherited-project",
    )
    context = AsyncMock()
    context.__aenter__.return_value = client
    factory = Mock(return_value=context)
    monkeypatch.setattr(module, "AsyncOpenAI", factory)
    await ResponsesService().search(
        "latest version?", "gpt-6.1-sol", api_key="offline-user-key"
    )
    assert factory.call_args.kwargs["api_key"] == "offline-user-key"
    assert "project" not in factory.call_args.kwargs
    assert "organization" not in factory.call_args.kwargs
    assert client.project is None
    assert client.organization is None


@pytest.mark.asyncio
async def test_real_sdk_request_does_not_forward_server_headers_for_user_key(
    monkeypatch,
):
    import json
    import httpx2
    from openai import AsyncOpenAI
    import services.responses_service as module

    requests = []

    def handle(request):
        requests.append(request)
        return httpx2.Response(200, json=api_response())

    monkeypatch.setenv("OPENAI_ORG_ID", "inherited-server-org")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "inherited-server-project")

    def make_client(**options):
        return AsyncOpenAI(
            **options,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle)),
        )

    monkeypatch.setattr(module, "AsyncOpenAI", make_client)
    result = await ResponsesService().search(
        "latest release?", "gpt-6.1-sol", api_key="offline-user-key"
    )
    assert result.citations
    assert requests[0].url.path.endswith("/responses")
    assert "OpenAI-Organization" not in requests[0].headers
    assert "OpenAI-Project" not in requests[0].headers
    assert json.loads(requests[0].content)["tool_choice"] == "required"


def test_incomplete_response_preserves_partial_text_and_explains_limit():
    response = api_response()
    response["status"] = "incomplete"
    result = parse_search_response(response)
    assert result.text.startswith("The release is current.")
    assert "incomplete" in result.text
    assert result.citations


def test_failed_response_does_not_echo_private_provider_error():
    response = api_response()
    response["status"] = "failed"
    response["error"] = {
        "code": "server_error",
        "message": "private provider debug detail",
    }
    with pytest.raises(ResponsesError) as exc:
        parse_search_response(response)
    assert "private provider" not in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("question", ["   ", "a" * 6001])
async def test_invalid_question_never_calls_api(question):
    client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock()))
    with pytest.raises(ValueError):
        await ResponsesService(client).search(question, "gpt-6.1-sol")
    client.responses.create.assert_not_called()


def test_citation_parsing_keeps_prose_and_sources_without_valid_spans():
    response = api_response()
    content = response["output"][-1]["content"][0]
    content["text"] = "A factual claim."
    content["annotations"] = [
        {
            "type": "url_citation",
            "start_index": 0,
            "end_index": 16,
            "title": "Source one",
            "url": "https://example.org/a(b)",
        },
        {
            "type": "url_citation",
            "start_index": -1,
            "end_index": 100,
            "title": "Source two",
            "url": "https://example.org/second",
        },
        {
            "type": "url_citation",
            "start_index": 0,
            "end_index": 0,
            "title": "Source one duplicate",
            "url": "https://example.org/a(b)",
        },
        {"type": "url_citation", "url": "javascript:alert(1)", "title": "Invalid"},
        {"type": "url_citation", "url": "https://[malformed", "title": "Invalid"},
    ]
    result = parse_search_response(response)
    assert result.text.startswith("A factual claim.")
    assert "[[1]](<https://example.org/a%28b%29>)" in result.text
    assert len(result.citations) == 2
    messages = discord_messages(result)
    assert any("https://example.org/second" in message for message in messages)
    assert all("javascript:" not in message for message in messages)


def test_long_answer_preserves_clickable_links_and_all_citations():
    link = "[[1]](<https://example.org/" + "a" * 150 + ">)"
    result = SearchResponse(
        "Answer " * 200 + link + " conclusion" * 150,
        (Citation("Source [one] @everyone", "https://example.org/source"),),
        "gpt-6.1-sol",
        {},
        1,
    )
    messages = discord_messages(result, limit=200)
    assert len(messages) > 1
    assert all(0 < len(message) <= 200 for message in messages)
    assert any(link in message for message in messages)
    assert any("https://example.org/source" in message for message in messages)
    assert any(r"Source \[one\]" in message for message in messages)


def test_oversized_source_link_is_reported_for_file_fallback():
    result = SearchResponse(
        "answer",
        (Citation("Source", "https://example.org/" + "x" * 2100),),
        "gpt-6.1-sol",
        {},
        1,
    )
    with pytest.raises(ValueError, match="source link"):
        discord_messages(result)


@pytest.fixture
def web_cog(monkeypatch, tmp_path):
    from services.environment_service import EnvService

    monkeypatch.setattr(EnvService, "get_allowed_guilds", lambda: [1234])
    monkeypatch.setattr(EnvService, "get_search_roles", lambda: [None])
    monkeypatch.setattr(EnvService, "get_user_input_api_keys", lambda: False)
    monkeypatch.setattr(EnvService, "get_openai_model", lambda: None)
    monkeypatch.setattr(EnvService, "save_path", lambda: tmp_path)
    from cogs.web_search_cog import WebSearchCog
    from models.openai_model import Models

    monkeypatch.setattr(Models, "WEB_SEARCH_MODELS", [Models.DEFAULT], raising=False)
    service = SimpleNamespace(
        search=AsyncMock(
            return_value=SearchResponse(
                "Current @everyone news",
                (),
                Models.DEFAULT,
                {"input_tokens": 1, "output_tokens": 2},
                1,
            )
        )
    )
    usage = SimpleNamespace(update_usage_from_response=AsyncMock())
    cog = WebSearchCog(None, usage_service=usage, responses_service=service)
    ctx = SimpleNamespace(
        user=SimpleNamespace(id=123, roles=[]), defer=AsyncMock(), respond=AsyncMock()
    )
    return cog, ctx


@pytest.mark.asyncio
async def test_web_sends_no_pings_and_records_usage(web_cog):
    cog, ctx = web_cog
    await cog.web.callback(cog, ctx, "latest news")
    ctx.defer.assert_awaited_once()
    assert ctx.respond.call_args.args == ("Current @everyone news",)
    mentions = ctx.respond.call_args.kwargs["allowed_mentions"]
    assert (
        mentions.everyone is False
        and mentions.roles is False
        and mentions.users is False
    )
    cog.usage_service.update_usage_from_response.assert_awaited_once()


@pytest.mark.asyncio
async def test_web_rejects_missing_role_before_paid_request(web_cog):
    cog, ctx = web_cog
    cog.search_roles = ["researcher"]
    await cog.web.callback(cog, ctx, "latest news")
    cog.responses_service.search.assert_not_awaited()
    assert ctx.respond.call_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_web_defaults_to_configured_custom_model(web_cog, monkeypatch):
    from services.environment_service import EnvService

    cog, ctx = web_cog
    monkeypatch.setattr(EnvService, "get_openai_model", lambda: "provider-deployment")
    await cog.web.callback(cog, ctx, "latest news")
    assert (
        cog.responses_service.search.call_args.kwargs["model"] == "provider-deployment"
    )


@pytest.mark.asyncio
async def test_web_explicit_model_overrides_configured_default(web_cog, monkeypatch):
    from services.environment_service import EnvService
    from models.openai_model import Models

    cog, ctx = web_cog
    monkeypatch.setattr(EnvService, "get_openai_model", lambda: "provider-deployment")
    await cog.web.callback(cog, ctx, "latest news", model=Models.DEFAULT)
    assert cog.responses_service.search.call_args.kwargs["model"] == Models.DEFAULT


@pytest.mark.asyncio
async def test_web_rejects_unconfigured_custom_model(web_cog):
    cog, ctx = web_cog
    await cog.web.callback(cog, ctx, "latest news", model="unconfigured-deployment")
    cog.responses_service.search.assert_not_awaited()
    assert ctx.respond.call_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_web_autocomplete_prioritizes_configured_model(web_cog, monkeypatch):
    from cogs.web_search_cog import web_models
    from services.environment_service import EnvService
    from models.openai_model import Models

    monkeypatch.setattr(EnvService, "get_openai_model", lambda: "provider-deployment")
    assert await web_models(SimpleNamespace(value="")) == [
        "provider-deployment",
        Models.DEFAULT,
    ]
    assert await web_models(SimpleNamespace(value="provider")) == [
        "provider-deployment"
    ]
    monkeypatch.setattr(EnvService, "get_openai_model", lambda: Models.DEFAULT)
    assert await web_models(SimpleNamespace(value="")) == [Models.DEFAULT]


@pytest.mark.asyncio
async def test_web_honors_user_key_configuration(web_cog, monkeypatch):
    from services.environment_service import EnvService
    from unittest.mock import Mock

    cog, ctx = web_cog
    database = Mock()
    database.get.return_value = "offline-test-user-key"
    monkeypatch.setattr(EnvService, "get_user_input_api_keys", lambda: True)
    monkeypatch.setattr(EnvService, "get_api_db", lambda: database)
    await cog.web.callback(cog, ctx, "latest news")
    assert (
        cog.responses_service.search.call_args.kwargs["api_key"]
        == "offline-test-user-key"
    )
    database.close.assert_called_once()
    assert "offline-test-user-key" not in str(ctx.respond.call_args_list)


@pytest.mark.asyncio
async def test_web_missing_user_key_never_bills_server_key(web_cog, monkeypatch):
    from services.environment_service import EnvService
    from unittest.mock import Mock

    cog, ctx = web_cog
    database = Mock()
    database.get.return_value = None
    monkeypatch.setattr(EnvService, "get_user_input_api_keys", lambda: True)
    monkeypatch.setattr(EnvService, "get_api_db", lambda: database)
    await cog.web.callback(cog, ctx, "latest news")
    cog.responses_service.search.assert_not_awaited()
    assert "/setup" in ctx.respond.call_args.args[0]
    assert ctx.respond.call_args.kwargs["ephemeral"] is True
    database.close.assert_called_once()


@pytest.mark.asyncio
async def test_usage_write_failure_does_not_discard_answer(web_cog):
    cog, ctx = web_cog
    cog.usage_service.update_usage_from_response.side_effect = OSError("disk full")
    await cog.web.callback(cog, ctx, "latest news")
    assert ctx.respond.call_args.args == ("Current @everyone news",)
