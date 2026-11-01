"""Exercise the base /web dispatcher through the real HTTP MCP client."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import uuid

import aiohttp
from aiohttp import web
import pytest

from services.environment_service import EnvService
from services.parallel_search_service import MCP_URL, ParallelSearchService
from services.responses_service import SearchResponse, ResponsesError


@pytest.fixture
async def mcp_server(monkeypatch):
    state = SimpleNamespace(
        requests=[], destinations=[], sessions=[], mode="json", status=200,
        tool_error=False, malformed=False, delay=False, text_content=False,
        factory_options=[], clients=[], fragmented=False, missing_tool=False, mismatch=False, oversized=False,
    )
    async def handle(request):
        payload = await request.json()
        state.requests.append({"payload": payload, "headers": dict(request.headers)})
        if state.delay:
            await asyncio.sleep(10)
        if state.status != 200:
            return web.Response(status=state.status, text="private upstream detail")
        method = payload["method"]
        headers = {}
        if method == "initialize":
            sid = uuid.uuid4().hex
            state.sessions.append(sid)
            headers["Mcp-Session-Id"] = sid
            result = {"protocolVersion": "2025-03-26", "capabilities": {}}
        elif method == "notifications/initialized":
            return web.Response(status=202)
        elif method == "tools/list":
            result = {"tools": [] if state.missing_tool else [{"name": "web_search"}]}
        else:
            data = {"results": [
                {"title": "[Release] @everyone", "url": "https://example.org/a(b)",
                 "excerpts": ["Current **release** facts. @everyone"]},
                {"title": "unsafe", "url": "javascript:alert(1)", "excerpts": ["unsafe"]},
                {"url": "https://[broken", "excerpts": []},
            ]}
            result = (
                {"content": [{"type": "text", "text": json.dumps(data)}]}
                if state.text_content else {"structuredContent": data}
            )
            if state.tool_error:
                result = {"isError": True, "content": [{"type": "text", "text": "private detail"}]}
        output = {"jsonrpc": "2.0", "id": payload["id"] + (1 if state.mismatch else 0), "result": result}
        if state.oversized:
            output["extra"] = "x" * 1_000_001
        if state.fragmented:
            response = web.StreamResponse(headers={"Content-Type": "application/json", **headers})
            await response.prepare(request)
            raw = json.dumps(output).encode()
            await response.write(raw[:5])
            await asyncio.sleep(0.001)
            await response.write(raw[5:])
            await response.write_eof()
            return response
        if state.malformed:
            return web.Response(text="not JSON", content_type="application/json")
        if state.mode == "sse":
            return web.Response(
                text=": ping\n\nevent: message\ndata: " + json.dumps(output) + "\n\n",
                content_type="text/event-stream", headers=headers,
            )
        return web.json_response(output, headers=headers)

    app = web.Application()
    app.router.add_post("/mcp", handle)
    runner = web.AppRunner(app, shutdown_timeout=0)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    real_session = aiohttp.ClientSession
    class RoutedSession:
        def __init__(self, **options):
            state.factory_options.append(options)
            self.session = real_session(**options)
            state.clients.append(self.session)
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            await self.session.close()
        def post(self, url, **options):
            state.destinations.append({
                "url": url, "payload": options["json"],
                "headers": dict(options["headers"]),
                "allow_redirects": options["allow_redirects"],
                "proxy": options["proxy"],
            })
            # The production client supplies its fixed destination and headers.
            # Route only the network boundary to this local HTTP fixture.
            options.pop("proxy")
            return self.session.post(f"http://127.0.0.1:{port}/mcp", **options)
    monkeypatch.setattr(aiohttp, "ClientSession", RoutedSession)
    monkeypatch.setattr(EnvService, "get_http_proxy", lambda url: None)
    try:
        yield state
    finally:
        await runner.cleanup()


@pytest.fixture
def command(monkeypatch, tmp_path):
    monkeypatch.setattr(EnvService, "get_allowed_guilds", lambda: [1234])
    monkeypatch.setattr(EnvService, "get_search_roles", lambda: [None])
    monkeypatch.setattr(EnvService, "get_user_input_api_keys", lambda: False)
    monkeypatch.setattr(EnvService, "get_openai_token", lambda: None)
    monkeypatch.setattr(EnvService, "get_openai_model", lambda: None)
    monkeypatch.setattr(EnvService, "save_path", lambda: tmp_path)
    monkeypatch.delenv("WEB_SEARCH_PROVIDER", raising=False)
    from cogs.web_search_cog import WebSearchCog
    responses = SimpleNamespace(search=AsyncMock(return_value=SearchResponse(
        "Synthesized answer", (), "gpt-6.1-sol", {}, 1,
    )))
    cog = WebSearchCog(None, responses_service=responses)
    ctx = SimpleNamespace(
        user=SimpleNamespace(id=123, roles=[]), defer=AsyncMock(), respond=AsyncMock(),
    )
    return cog, ctx


@pytest.mark.parametrize("mode", ["json", "sse"])
@pytest.mark.parametrize("text_content", [False, True])
async def test_no_key_dispatches_mcp_and_delivers_safe_source_excerpts(
    command, mcp_server, monkeypatch, mode, text_content,
):
    cog, ctx = command
    mcp_server.mode, mcp_server.text_content = mode, text_content
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-must-not-be-forwarded")
    monkeypatch.setenv("PARALLEL_API_KEY", "ambient-must-not-be-forwarded")
    await cog.web.callback(cog, ctx, "latest release")
    cog.responses_service.search.assert_not_awaited()
    assert [r["payload"]["method"] for r in mcp_server.requests] == [
        "initialize", "notifications/initialized", "tools/list", "tools/call",
    ]
    assert all(r["url"] == MCP_URL for r in mcp_server.destinations)
    assert all(r["allow_redirects"] is False for r in mcp_server.destinations)
    assert all(r["headers"]["User-Agent"] == "GPTDiscord/13.2.6" for r in mcp_server.requests)
    assert all("Authorization" not in r["headers"] for r in mcp_server.requests)
    assert all(o["trust_env"] is False for o in mcp_server.factory_options)
    transport_id = mcp_server.sessions[0]
    assert all(r["headers"]["Mcp-Session-Id"] == transport_id for r in mcp_server.requests[1:])
    assert all(r["headers"]["MCP-Protocol-Version"] == "2025-03-26" for r in mcp_server.requests[1:])
    args = mcp_server.requests[-1]["payload"]["params"]["arguments"]
    assert args["objective"] == "latest release"
    assert args["search_queries"] == ["latest release"]
    assert len(args["session_id"]) == 32
    assert args["session_id"] != transport_id
    assert "model_name" not in args  # No model makes this standalone source lookup.
    delivered = "\n".join(call.args[0] for call in ctx.respond.call_args_list)
    assert "Current \\*\\*release\\*\\* facts." in delivered
    assert "[\\[Release\\] @everyone](<https://example.org/a%28b%29>)" in delivered
    assert "javascript:" not in delivered
    assert "Synthesized answer" not in delivered
    assert "unsafe" not in delivered
    assert all(call.kwargs["allowed_mentions"].everyone is False for call in ctx.respond.call_args_list)


async def test_concurrent_standalone_commands_own_separate_run_ids(command, mcp_server):
    cog, ctx = command
    ctx2 = SimpleNamespace(user=ctx.user, defer=AsyncMock(), respond=AsyncMock())
    await asyncio.gather(
        cog.web.callback(cog, ctx, "first lookup"),
        cog.web.callback(cog, ctx2, "second lookup"),
    )
    calls = [r["payload"]["params"]["arguments"] for r in mcp_server.requests if r["payload"]["method"] == "tools/call"]
    assert {c["objective"] for c in calls} == {"first lookup", "second lookup"}
    assert len({c["session_id"] for c in calls}) == 2
    await cog.web.callback(cog, ctx, "new lookup")
    calls = [r["payload"]["params"]["arguments"] for r in mcp_server.requests if r["payload"]["method"] == "tools/call"]
    assert len({c["session_id"] for c in calls}) == 3


@pytest.mark.parametrize("setting", ["OPENAI_API_KEY", "OPENAI_TOKEN"])
async def test_auto_preserves_host_key_aliases(command, mcp_server, monkeypatch, setting):
    cog, ctx = command
    monkeypatch.setattr(EnvService, "get_openai_token", staticmethod(
        lambda: __import__("os").getenv("OPENAI_API_KEY") or __import__("os").getenv("OPENAI_TOKEN")
    ))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_TOKEN", raising=False)
    monkeypatch.setenv(setting, "offline-host-key")
    await cog.web.callback(cog, ctx, "question")
    cog.responses_service.search.assert_awaited_once()
    assert not mcp_server.requests


async def test_saved_personal_key_remains_openai_and_database_closes(command, mcp_server, monkeypatch):
    cog, ctx = command
    database = Mock()
    database.get.return_value = "offline-personal-key"
    monkeypatch.setattr(EnvService, "get_user_input_api_keys", lambda: True)
    monkeypatch.setattr(EnvService, "get_api_db", lambda: database)
    await cog.web.callback(cog, ctx, "question")
    assert cog.responses_service.search.call_args.kwargs["api_key"] == "offline-personal-key"
    database.close.assert_called_once()
    assert not mcp_server.requests


async def test_missing_personal_key_uses_parallel_without_host_key(command, mcp_server, monkeypatch):
    cog, ctx = command
    database = Mock()
    database.get.return_value = None
    monkeypatch.setattr(EnvService, "get_user_input_api_keys", lambda: True)
    monkeypatch.setattr(EnvService, "get_openai_token", lambda: "host-must-not-be-used")
    monkeypatch.setattr(EnvService, "get_api_db", lambda: database)
    await cog.web.callback(cog, ctx, "question")
    cog.responses_service.search.assert_not_awaited()
    assert len(mcp_server.requests) == 4
    database.close.assert_called_once()


async def test_explicit_parallel_bypasses_configured_credentials(command, mcp_server, monkeypatch):
    cog, ctx = command
    monkeypatch.setattr(EnvService, "get_openai_token", lambda: "offline-host-key")
    monkeypatch.setattr(EnvService, "get_user_input_api_keys", lambda: True)
    database = Mock()
    monkeypatch.setattr(EnvService, "get_api_db", database)
    await cog.web.callback(cog, ctx, "question", provider="parallel")
    database.assert_not_called()
    cog.responses_service.search.assert_not_awaited()
    assert len(mcp_server.requests) == 4


@pytest.mark.parametrize("kwargs", [{"provider": "openai"}, {"model": "gpt-6.1-sol"}])
async def test_explicit_openai_or_model_keeps_personal_key_requirement(command, mcp_server, monkeypatch, kwargs):
    cog, ctx = command
    database = Mock()
    database.get.return_value = None
    monkeypatch.setattr(EnvService, "get_user_input_api_keys", lambda: True)
    monkeypatch.setattr(EnvService, "get_api_db", lambda: database)
    await cog.web.callback(cog, ctx, "question", **kwargs)
    cog.responses_service.search.assert_not_awaited()
    assert "/setup" in ctx.respond.call_args.args[0]
    assert not mcp_server.requests


async def test_environment_model_is_preserved_without_key(command, mcp_server, monkeypatch):
    cog, ctx = command
    monkeypatch.setattr(EnvService, "get_openai_model", lambda: "custom-deployment")
    await cog.web.callback(cog, ctx, "question")
    assert cog.responses_service.search.call_args.kwargs["model"] == "custom-deployment"
    assert not mcp_server.requests


@pytest.mark.parametrize("setting,kwargs,expected", [
    ("parallel", {}, "parallel"),
    ("openai", {}, "openai"),
    ("parallel", {"model": "gpt-6.1-sol"}, "openai"),
    ("openai", {"provider": "parallel"}, "parallel"),
])
async def test_environment_selection_and_command_overrides(command, mcp_server, monkeypatch, setting, kwargs, expected):
    cog, ctx = command
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", setting)
    await cog.web.callback(cog, ctx, "question", **kwargs)
    assert bool(mcp_server.requests) == (expected == "parallel")
    assert cog.responses_service.search.await_count == (expected == "openai")


@pytest.mark.parametrize("setting,kwargs", [
    ("disabled", {}), ("disabled", {"provider": "parallel"}),
    ("unknown", {}), ("auto", {"provider": "unknown"}),
    ("auto", {"provider": "parallel", "model": "gpt-6.1-sol"}),
])
async def test_disabled_or_invalid_selection_does_not_call_either_provider(command, mcp_server, monkeypatch, setting, kwargs):
    cog, ctx = command
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", setting)
    await cog.web.callback(cog, ctx, "question", **kwargs)
    cog.responses_service.search.assert_not_awaited()
    assert not mcp_server.requests
    assert ctx.respond.call_args.kwargs["ephemeral"]


async def test_role_restriction_precedes_parallel_calls(command, mcp_server):
    cog, ctx = command
    cog.search_roles = ["researcher"]
    await cog.web.callback(cog, ctx, "question", provider="parallel")
    assert not mcp_server.requests
    assert "SEARCH_ROLES" in ctx.respond.call_args.args[0]


@pytest.mark.parametrize("failure", ["http", "quota", "tool", "malformed"])
async def test_parallel_errors_are_safe_and_never_fall_back(command, mcp_server, failure):
    cog, ctx = command
    if failure == "http":
        mcp_server.status = 500
    elif failure == "quota":
        mcp_server.status = 429
    elif failure == "tool":
        mcp_server.tool_error = True
    else:
        mcp_server.malformed = True
    await cog.web.callback(cog, ctx, "question", provider="parallel")
    cog.responses_service.search.assert_not_awaited()
    text = ctx.respond.call_args.args[0]
    assert "Parallel" in text
    assert "private" not in text
    assert ctx.respond.call_args.kwargs["ephemeral"]


async def test_timeout_is_safe_without_fallback(command, monkeypatch):
    cog, ctx = command
    class TimeoutSession:
        async def __aenter__(self):
            raise TimeoutError
        async def __aexit__(self, *args):
            pass
    monkeypatch.setattr(aiohttp, "ClientSession", lambda **options: TimeoutSession())
    await cog.web.callback(cog, ctx, "question", provider="parallel")
    cog.responses_service.search.assert_not_awaited()
    assert "Could not reach Parallel" in ctx.respond.call_args.args[0]


async def test_cancellation_propagates_and_closes_http_session(command, mcp_server):
    cog, ctx = command
    mcp_server.delay = True
    task = asyncio.create_task(cog.web.callback(cog, ctx, "question", provider="parallel"))
    for _ in range(100):
        if mcp_server.requests:
            break
        await asyncio.sleep(0.001)
    assert mcp_server.requests
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    cog.responses_service.search.assert_not_awaited()
    ctx.respond.assert_not_awaited()
    assert all(client.closed for client in mcp_server.clients)


@pytest.mark.parametrize("question", ["  ", "x" * 6001])
async def test_invalid_questions_make_no_http_request(command, mcp_server, question):
    cog, ctx = command
    await cog.web.callback(cog, ctx, question, provider="parallel")
    assert not mcp_server.requests
    assert ctx.respond.call_args.kwargs["ephemeral"]


async def test_fragmented_json_body_is_read_completely(command, mcp_server):
    cog, ctx = command
    mcp_server.fragmented = True
    await cog.web.callback(cog, ctx, "question", provider="parallel")
    assert "Web source excerpts" in ctx.respond.call_args.args[0]
    assert len(mcp_server.requests) == 4


@pytest.mark.parametrize("flag", ["missing_tool", "mismatch", "oversized"])
async def test_protocol_failures_are_bounded_and_safe(command, mcp_server, flag):
    cog, ctx = command
    setattr(mcp_server, flag, True)
    await cog.web.callback(cog, ctx, "question", provider="parallel")
    assert ctx.respond.call_args.kwargs["ephemeral"]
    cog.responses_service.search.assert_not_awaited()
    assert len(mcp_server.requests) <= 3


async def test_large_excerpt_delivery_uses_existing_file_fallback(command, monkeypatch):
    cog, ctx = command
    from services.responses_service import Citation
    cog.parallel_service = SimpleNamespace(search=AsyncMock(return_value=SearchResponse(
        "excerpt " * 2000, (Citation("source", "https://example.org"),), "", {}, 1,
    )))
    await cog.web.callback(cog, ctx, "question", provider="parallel")
    assert "attached" in ctx.respond.call_args.args[0]
    file = ctx.respond.call_args.kwargs["file"]
    assert file.filename == "web-search.txt"
    assert b"https://example.org" in file.fp.getvalue()
