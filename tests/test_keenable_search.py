"""Exercise the base /web Keenable dispatcher through a local HTTP fixture."""

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
from aiohttp import web
import pytest

from services.environment_service import EnvService
from services.keenable_search_service import (
    PUBLIC_SEARCH_URL, SEARCH_URL, KeenableSearchService,
)
from services.responses_service import SearchResponse


@pytest.fixture
async def keenable_server(monkeypatch):
    state = SimpleNamespace(
        requests=[], destinations=[], factory_options=[], status=200,
        malformed=False, empty=False, oversized=False,
    )

    async def handle(request):
        state.requests.append({
            "payload": await request.json(), "headers": dict(request.headers),
        })
        if state.status != 200:
            return web.Response(status=state.status, text="private upstream detail")
        if state.malformed:
            return web.Response(text="not JSON", content_type="application/json")
        if state.oversized:
            return web.Response(body=b"x" * 1_000_001, content_type="application/json")
        results = [] if state.empty else [
            {"title": "[Release] @everyone", "url": "https://example.org/a(b)",
             "snippet": "Current **release** facts. @everyone"},
            {"title": "unsafe", "url": "javascript:alert(1)", "snippet": "unsafe"},
            {"title": "duplicate", "url": "https://example.org/a(b)", "snippet": "dup"},
            {"url": "https://example.com/untitled", "snippet": None},
        ]
        return web.json_response({"query": "q", "mode": "pro", "results": results})

    app = web.Application()
    app.router.add_post("/search", handle)
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

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            await self.session.close()

        def post(self, url, **options):
            state.destinations.append({
                "url": url, "allow_redirects": options["allow_redirects"],
                "headers": dict(options["headers"]),
            })
            # Route only the network boundary to this local HTTP fixture.
            options.pop("proxy")
            return self.session.post(f"http://127.0.0.1:{port}/search", **options)

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
    monkeypatch.delenv("KEENABLE_API_KEY", raising=False)
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


def delivered(ctx):
    return "\n".join(call.args[0] for call in ctx.respond.call_args_list)


async def test_keyless_request_uses_public_endpoint_and_delivers_safe_sources(
    command, keenable_server, monkeypatch,
):
    cog, ctx = command
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-must-not-be-forwarded")
    await cog.web.callback(cog, ctx, "latest release", provider="keenable")
    cog.responses_service.search.assert_not_awaited()
    cog.parallel_service.search.assert_not_awaited()
    [destination] = keenable_server.destinations
    assert destination["url"] == PUBLIC_SEARCH_URL
    assert destination["allow_redirects"] is False
    assert all(o["trust_env"] is False for o in keenable_server.factory_options)
    [request] = keenable_server.requests
    assert request["payload"] == {"query": "latest release", "max_results": 5}
    assert request["headers"]["X-Keenable-Title"] == "GPTDiscord"
    assert request["headers"]["User-Agent"] == "GPTDiscord/13.2.6"
    assert "X-API-Key" not in request["headers"]
    assert "Authorization" not in request["headers"]
    text = delivered(ctx)
    assert "Web source excerpts from Keenable Search" in text
    assert "Current \\*\\*release\\*\\* facts." in text
    assert "[\\[Release\\] @everyone](<https://example.org/a%28b%29>)" in text
    assert "https://example.com/untitled" in text
    assert "javascript:" not in text
    assert "dup" not in text
    assert all(
        call.kwargs["allowed_mentions"].everyone is False
        for call in ctx.respond.call_args_list
    )


async def test_api_key_uses_keyed_endpoint(command, keenable_server, monkeypatch):
    cog, ctx = command
    monkeypatch.setenv("KEENABLE_API_KEY", " keen_test ")
    await cog.web.callback(cog, ctx, "question", provider="keenable")
    assert keenable_server.destinations[0]["url"] == SEARCH_URL
    headers = keenable_server.requests[0]["headers"]
    assert headers["X-API-Key"] == "keen_test"
    assert "X-Keenable-Title" not in headers


@pytest.mark.parametrize("setting,kwargs,expected", [
    ("keenable", {}, "keenable"),
    ("keenable", {"model": "gpt-6.1-sol"}, "openai"),
    ("keenable", {"provider": "parallel"}, "parallel"),
    ("openai", {"provider": "keenable"}, "keenable"),
    ("auto", {}, "parallel"),
])
async def test_environment_selection_and_command_overrides(
    command, keenable_server, monkeypatch, setting, kwargs, expected,
):
    cog, ctx = command
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", setting)
    await cog.web.callback(cog, ctx, "question", **kwargs)
    assert bool(keenable_server.requests) == (expected == "keenable")
    assert cog.parallel_service.search.await_count == (expected == "parallel")
    assert cog.responses_service.search.await_count == (expected == "openai")


@pytest.mark.parametrize("setting,kwargs", [
    ("disabled", {"provider": "keenable"}),
    ("auto", {"provider": "keenable", "model": "gpt-6.1-sol"}),
])
async def test_disabled_or_invalid_selection_makes_no_request(
    command, keenable_server, monkeypatch, setting, kwargs,
):
    cog, ctx = command
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", setting)
    await cog.web.callback(cog, ctx, "question", **kwargs)
    assert not keenable_server.requests
    assert ctx.respond.call_args.kwargs["ephemeral"]


async def test_role_restriction_precedes_keenable_calls(command, keenable_server):
    cog, ctx = command
    cog.search_roles = ["researcher"]
    await cog.web.callback(cog, ctx, "question", provider="keenable")
    assert not keenable_server.requests
    assert "SEARCH_ROLES" in ctx.respond.call_args.args[0]


@pytest.mark.parametrize("failure,message", [
    ("http", "could not complete"), ("quota", "rate limit"),
    ("auth", "KEENABLE_API_KEY"), ("malformed", "invalid search response"),
    ("oversized", "too much search data"),
])
async def test_errors_are_safe_and_never_fall_back(command, keenable_server, failure, message):
    cog, ctx = command
    if failure == "http":
        keenable_server.status = 500
    elif failure == "quota":
        keenable_server.status = 429
    elif failure == "auth":
        keenable_server.status = 401
    else:
        setattr(keenable_server, failure, True)
    await cog.web.callback(cog, ctx, "question", provider="keenable")
    cog.responses_service.search.assert_not_awaited()
    cog.parallel_service.search.assert_not_awaited()
    assert message in ctx.respond.call_args.args[0]
    assert "private upstream detail" not in delivered(ctx)
    assert ctx.respond.call_args.kwargs["ephemeral"]


async def test_no_results_message(command, keenable_server):
    cog, ctx = command
    keenable_server.empty = True
    await cog.web.callback(cog, ctx, "question", provider="keenable")
    assert "Keenable found no web sources" in delivered(ctx)


@pytest.mark.parametrize("question", ["   ", "x" * 6001])
async def test_invalid_questions_make_no_http_request(command, keenable_server, question):
    cog, ctx = command
    await cog.web.callback(cog, ctx, question, provider="keenable")
    assert not keenable_server.requests


async def test_timeout_is_safe(command, monkeypatch):
    cog, ctx = command

    class Hanging:
        def __init__(self, **options):
            pass

        async def __aenter__(self):
            raise asyncio.TimeoutError

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(aiohttp, "ClientSession", Hanging)
    await cog.web.callback(cog, ctx, "question", provider="keenable")
    assert "Could not reach Keenable" in ctx.respond.call_args.args[0]


@pytest.mark.skipif(not os.getenv("KEENABLE_LIVE"), reason="set KEENABLE_LIVE=1 for a live call")
async def test_live_keyless_search():
    result = await KeenableSearchService().search("Discord bot slash commands documentation")
    assert result.citations
    assert all(c.url.startswith("https://") for c in result.citations)
