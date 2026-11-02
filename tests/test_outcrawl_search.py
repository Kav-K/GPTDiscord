"""Exercise /web provider:outcrawl through the real HTTP client."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import aiohttp
from aiohttp import web
import pytest

from services.environment_service import EnvService
from services.outcrawl_search_service import SEARCH_URL
from services.responses_service import SearchResponse


@pytest.fixture
async def outcrawl_server(monkeypatch):
    state = SimpleNamespace(
        requests=[], destinations=[], factory_options=[], clients=[],
        status=200, malformed=False, delay=False, oversized=False, empty=False,
    )

    async def handle(request):
        state.requests.append({"payload": await request.json(), "headers": dict(request.headers)})
        if state.delay:
            await asyncio.sleep(10)
        if state.status != 200:
            return web.Response(status=state.status, text="private upstream detail")
        if state.malformed:
            return web.Response(text="not JSON", content_type="application/json")
        hits = [] if state.empty else [
            {"title": "[Release] @everyone", "url": "https://example.org/a(b)",
             "snippet": "Current **release** facts. @everyone", "rank": 1},
            {"title": "unsafe", "url": "javascript:alert(1)", "snippet": "unsafe", "rank": 2},
            {"title": "duplicate", "url": "https://example.org/a(b)", "snippet": "dup", "rank": 3},
            {"url": "https://example.net/untitled", "rank": 4},
        ]
        output = {"hits": hits, "usage": {"credits": "2"}}
        if state.oversized:
            output["extra"] = "x" * 1_000_001
        return web.json_response(output)

    app = web.Application()
    app.router.add_post("/v1/search", handle)
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
            return self.session.post(f"http://127.0.0.1:{port}/v1/search", **options)

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
    monkeypatch.setenv("OUTCRAWL_API_KEY", " offline-outcrawl-key ")
    from cogs.web_search_cog import WebSearchCog
    responses = SimpleNamespace(search=AsyncMock(return_value=SearchResponse(
        "Synthesized answer", (), "gpt-6.1-sol", {}, 1,
    )))
    parallel = SimpleNamespace(search=AsyncMock())
    cog = WebSearchCog(None, responses_service=responses, parallel_service=parallel)
    ctx = SimpleNamespace(
        user=SimpleNamespace(id=123, roles=[]), defer=AsyncMock(), respond=AsyncMock(),
    )
    return cog, ctx


async def test_explicit_outcrawl_sends_one_search_and_delivers_safe_source_excerpts(
    command, outcrawl_server, monkeypatch,
):
    cog, ctx = command
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-must-not-be-forwarded")
    await cog.web.callback(cog, ctx, "  latest release  ", provider="outcrawl")
    cog.responses_service.search.assert_not_awaited()
    cog.parallel_service.search.assert_not_awaited()
    assert len(outcrawl_server.requests) == 1
    request = outcrawl_server.requests[0]
    assert request["payload"] == {"query": "latest release", "limit": 5}
    assert request["headers"]["Authorization"] == "Bearer offline-outcrawl-key"
    assert request["headers"]["User-Agent"] == "GPTDiscord/13.2.6"
    assert all(d["url"] == SEARCH_URL for d in outcrawl_server.destinations)
    assert all(d["allow_redirects"] is False for d in outcrawl_server.destinations)
    assert all(o["trust_env"] is False for o in outcrawl_server.factory_options)
    delivered = "\n".join(call.args[0] for call in ctx.respond.call_args_list)
    assert "Web source excerpts from Outcrawl Search" in delivered
    assert "Current \\*\\*release\\*\\* facts." in delivered
    assert "[\\[Release\\] @everyone](<https://example.org/a%28b%29>)" in delivered
    assert "https://example.net/untitled" in delivered
    assert "No excerpt available." in delivered
    assert "javascript:" not in delivered
    assert "unsafe" not in delivered
    assert "dup" not in delivered
    assert "Synthesized answer" not in delivered
    assert all(call.kwargs["allowed_mentions"].everyone is False for call in ctx.respond.call_args_list)


async def test_environment_default_selects_outcrawl(command, outcrawl_server, monkeypatch):
    cog, ctx = command
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "outcrawl")
    await cog.web.callback(cog, ctx, "question")
    assert len(outcrawl_server.requests) == 1
    cog.responses_service.search.assert_not_awaited()
    cog.parallel_service.search.assert_not_awaited()


@pytest.mark.parametrize("setting,kwargs,expected", [
    ("outcrawl", {"model": "gpt-6.1-sol"}, "openai"),
    ("outcrawl", {"provider": "parallel"}, "parallel"),
    ("openai", {"provider": "outcrawl"}, "outcrawl"),
    ("auto", {}, "parallel"),
])
async def test_command_options_override_the_environment(
    command, outcrawl_server, monkeypatch, setting, kwargs, expected,
):
    cog, ctx = command
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", setting)
    await cog.web.callback(cog, ctx, "question", **kwargs)
    assert bool(outcrawl_server.requests) == (expected == "outcrawl")
    assert cog.parallel_service.search.await_count == (expected == "parallel")
    assert cog.responses_service.search.await_count == (expected == "openai")


async def test_explicit_outcrawl_never_reads_personal_openai_keys(command, outcrawl_server, monkeypatch):
    cog, ctx = command
    monkeypatch.setattr(EnvService, "get_user_input_api_keys", lambda: True)
    database = Mock()
    monkeypatch.setattr(EnvService, "get_api_db", database)
    await cog.web.callback(cog, ctx, "question", provider="outcrawl")
    database.assert_not_called()
    assert len(outcrawl_server.requests) == 1


@pytest.mark.parametrize("setting,kwargs", [
    ("disabled", {"provider": "outcrawl"}),
    ("auto", {"provider": "outcrawl", "model": "gpt-6.1-sol"}),
])
async def test_disabled_or_invalid_selection_does_not_call_outcrawl(
    command, outcrawl_server, monkeypatch, setting, kwargs,
):
    cog, ctx = command
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", setting)
    await cog.web.callback(cog, ctx, "question", **kwargs)
    assert not outcrawl_server.requests
    assert ctx.respond.call_args.kwargs["ephemeral"]


@pytest.mark.parametrize("value", [None, "   "])
async def test_missing_key_is_refused_before_any_request(command, outcrawl_server, monkeypatch, value):
    cog, ctx = command
    if value is None:
        monkeypatch.delenv("OUTCRAWL_API_KEY")
    else:
        monkeypatch.setenv("OUTCRAWL_API_KEY", value)
    await cog.web.callback(cog, ctx, "question", provider="outcrawl")
    assert not outcrawl_server.requests
    ctx.defer.assert_not_awaited()
    assert "OUTCRAWL_API_KEY" in ctx.respond.call_args.args[0]
    assert ctx.respond.call_args.kwargs["ephemeral"]


async def test_role_restriction_precedes_outcrawl_calls(command, outcrawl_server):
    cog, ctx = command
    cog.search_roles = ["researcher"]
    await cog.web.callback(cog, ctx, "question", provider="outcrawl")
    assert not outcrawl_server.requests
    assert "SEARCH_ROLES" in ctx.respond.call_args.args[0]


@pytest.mark.parametrize("status,expected", [
    (401, "OUTCRAWL_API_KEY"), (403, "OUTCRAWL_API_KEY"), (402, "out of credits"),
    (429, "rate limit"), (500, "could not complete"),
])
async def test_http_errors_are_safe_and_never_fall_back(command, outcrawl_server, status, expected):
    cog, ctx = command
    outcrawl_server.status = status
    await cog.web.callback(cog, ctx, "question", provider="outcrawl")
    cog.responses_service.search.assert_not_awaited()
    cog.parallel_service.search.assert_not_awaited()
    text = ctx.respond.call_args.args[0]
    assert "Outcrawl" in text and expected in text
    assert "private" not in text
    assert ctx.respond.call_args.kwargs["ephemeral"]


@pytest.mark.parametrize("flag", ["malformed", "oversized"])
async def test_invalid_bodies_are_bounded_and_safe(command, outcrawl_server, flag):
    cog, ctx = command
    setattr(outcrawl_server, flag, True)
    await cog.web.callback(cog, ctx, "question", provider="outcrawl")
    assert "Outcrawl returned" in ctx.respond.call_args.args[0]
    assert ctx.respond.call_args.kwargs["ephemeral"]


async def test_no_hits_reports_no_sources(command, outcrawl_server):
    cog, ctx = command
    outcrawl_server.empty = True
    await cog.web.callback(cog, ctx, "question", provider="outcrawl")
    assert "Outcrawl found no web sources" in ctx.respond.call_args.args[0]


async def test_timeout_is_safe_without_fallback(command, monkeypatch):
    cog, ctx = command

    class TimeoutSession:
        async def __aenter__(self):
            raise TimeoutError

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(aiohttp, "ClientSession", lambda **options: TimeoutSession())
    await cog.web.callback(cog, ctx, "question", provider="outcrawl")
    cog.responses_service.search.assert_not_awaited()
    assert "Could not reach Outcrawl" in ctx.respond.call_args.args[0]


async def test_cancellation_propagates_and_closes_http_session(command, outcrawl_server):
    cog, ctx = command
    outcrawl_server.delay = True
    task = asyncio.create_task(cog.web.callback(cog, ctx, "question", provider="outcrawl"))
    for _ in range(100):
        if outcrawl_server.requests:
            break
        await asyncio.sleep(0.001)
    assert outcrawl_server.requests
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    ctx.respond.assert_not_awaited()
    assert all(client.closed for client in outcrawl_server.clients)


@pytest.mark.parametrize("question", ["  ", "x" * 6001])
async def test_invalid_questions_make_no_http_request(command, outcrawl_server, question):
    cog, ctx = command
    await cog.web.callback(cog, ctx, question, provider="outcrawl")
    assert not outcrawl_server.requests
    assert ctx.respond.call_args.kwargs["ephemeral"]


def test_environment_accepts_outcrawl(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", " Outcrawl ")
    assert EnvService.get_web_search_provider() == "outcrawl"
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "unknown")
    with pytest.raises(ValueError, match="outcrawl"):
        EnvService.get_web_search_provider()
