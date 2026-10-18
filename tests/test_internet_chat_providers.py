"""Native and legacy internet-chat providers without paid APIs or Discord I/O."""

import asyncio
import importlib
import json
import os
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("llama_index.core")
pytest.importorskip("langchain_classic")
pytest.importorskip("langchain_openai")
pytest.importorskip("langchain_community")
httpx = pytest.importorskip("httpx")


@pytest.fixture
def internet(monkeypatch, tmp_path):
    monkeypatch.setenv("ALLOWED_GUILDS", "1")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SHARE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "offline-openai-key")
    module = importlib.import_module("cogs.search_service_cog")
    monkeypatch.setattr(module, "USER_INPUT_API_KEYS", False)
    monkeypatch.setattr(module, "PRE_MODERATE", False)
    monkeypatch.setattr(module, "GOOGLE_API_KEY", None)
    monkeypatch.setattr(module, "GOOGLE_SEARCH_ENGINE_ID", None)
    monkeypatch.setattr(module, "safe_ctx_respond", AsyncMock())
    return module


def command_context():
    thread = SimpleNamespace(id=123, send=AsyncMock(), edit=AsyncMock())
    message = SimpleNamespace(create_thread=AsyncMock(return_value=thread))
    ctx = SimpleNamespace(
        user=SimpleNamespace(id=42, name="Tester"),
        guild_id=1,
        defer=AsyncMock(),
        respond=AsyncMock(),
        send=AsyncMock(return_value=message),
    )
    return ctx, thread, message


def capture_agent(internet, monkeypatch):
    llm = Mock(return_value=object())
    agent = SimpleNamespace(memory=SimpleNamespace())
    build = Mock(return_value=agent)
    monkeypatch.setattr(internet, "create_chat_llm", llm)
    monkeypatch.setattr(internet, "create_chat_agent", build)
    return llm, build, agent


@pytest.mark.asyncio
@pytest.mark.parametrize("personal_key", [None, "personal-openai-key"])
async def test_default_internet_chat_uses_bounded_native_web_without_google(
    internet, monkeypatch, personal_key
):
    from services.agent_service import personal_key_fingerprint
    from services.text_service import TextService

    llm, build, agent = capture_agent(internet, monkeypatch)
    google = Mock(side_effect=AssertionError("Native search must not construct Google"))
    monkeypatch.setattr(internet, "GoogleSearchAPIWrapper", google)
    monkeypatch.setattr(internet, "USER_INPUT_API_KEYS", bool(personal_key))
    monkeypatch.setattr(TextService, "get_user_api_key", AsyncMock(return_value=personal_key))
    cog = internet.SearchService(None, None, None, None, None)
    ctx, thread, _ = command_context()

    await cog.search_chat_command(ctx, model="gpt-6.1-sol", search_scope=3)

    google.assert_not_called()
    assert llm.call_args.kwargs["model"] == "gpt-6.1-sol"
    assert llm.call_args.kwargs["api_key"] == personal_key
    assert llm.call_args.kwargs["model_kwargs"] == {"max_tool_calls": 3}
    assert llm.call_args.kwargs["max_tokens"] == 4096
    assert llm.call_args.kwargs["output_version"] == "responses/v1"
    assert llm.call_args.kwargs["store"] is False
    assert build.call_args.kwargs["builtin_tools"] == [
        {"type": "web_search", "external_web_access": True}
    ]
    local_tools = build.call_args.args[1]
    assert any(tool.name == "Web-Crawling-Tool" for tool in local_tools)
    assert all(tool.name != "Search-Tool" for tool in local_tools)
    assert cog.chat_agents[thread.id] is agent
    assert cog.thread_owners[thread.id] == ctx.user.id
    if personal_key:
        assert cog.thread_key_fingerprints[thread.id] == personal_key_fingerprint(personal_key)


@pytest.mark.asyncio
async def test_explicit_google_provider_preserves_google_and_crawler_tools(internet, monkeypatch):
    llm, build, _ = capture_agent(internet, monkeypatch)
    monkeypatch.setattr(internet, "GOOGLE_API_KEY", "offline-google-key")
    monkeypatch.setattr(internet, "GOOGLE_SEARCH_ENGINE_ID", "offline-google-engine")
    google = Mock(return_value=SimpleNamespace(run=Mock(return_value="Search results")))
    monkeypatch.setattr(internet, "GoogleSearchAPIWrapper", google)
    cog = internet.SearchService(None, None, None, None, None)
    ctx, thread, _ = command_context()

    await cog.search_chat_command(ctx, model="gpt-6-luna", search_scope=4, provider="google")

    google.assert_called_once_with(
        google_api_key="offline-google-key", google_cse_id="offline-google-engine", k=4
    )
    assert {tool.name for tool in build.call_args.args[1]} == {"Search-Tool", "Web-Crawling-Tool"}
    assert not build.call_args.kwargs.get("builtin_tools")
    assert "max_tool_calls" not in llm.call_args.kwargs
    assert thread.id in cog.chat_agents


@pytest.mark.asyncio
@pytest.mark.parametrize("google_key,engine", [(None, None), ("key", None), (None, "engine")])
async def test_google_requires_both_credentials_before_creating_a_thread(internet, monkeypatch, google_key, engine):
    llm, build, _ = capture_agent(internet, monkeypatch)
    monkeypatch.setattr(internet, "GOOGLE_API_KEY", google_key)
    monkeypatch.setattr(internet, "GOOGLE_SEARCH_ENGINE_ID", engine)
    google = Mock(side_effect=AssertionError("Missing credentials must be rejected first"))
    monkeypatch.setattr(internet, "GoogleSearchAPIWrapper", google)
    cog = internet.SearchService(None, None, None, None, None)
    ctx, _, starter = command_context()

    await cog.search_chat_command(ctx, model="gpt-6-luna", provider="google")

    ctx.respond.assert_awaited_once()
    ctx.send.assert_not_awaited()
    starter.create_thread.assert_not_awaited()
    llm.assert_not_called()
    build.assert_not_called()
    google.assert_not_called()
    assert not cog.chat_agents


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,model", [("unknown-provider", "gpt-6.1-sol"), ("openai", "not-a-web-model")])
async def test_invalid_provider_or_native_model_does_not_create_dead_threads(internet, monkeypatch, provider, model):
    llm, build, _ = capture_agent(internet, monkeypatch)
    cog = internet.SearchService(None, None, None, None, None)
    ctx, _, starter = command_context()

    await cog.search_chat_command(ctx, model=model, provider=provider)

    ctx.respond.assert_awaited_once()
    ctx.send.assert_not_awaited()
    starter.create_thread.assert_not_awaited()
    llm.assert_not_called()
    build.assert_not_called()


@pytest.mark.asyncio
async def test_configured_custom_web_model_is_available_to_native_internet_chat(internet, monkeypatch):
    from services.environment_service import EnvService

    monkeypatch.setattr(EnvService, "get_openai_model", lambda: "custom-web-model")
    llm, _, _ = capture_agent(internet, monkeypatch)
    cog = internet.SearchService(None, None, None, None, None)
    ctx, thread, _ = command_context()

    await cog.search_chat_command(ctx, model="custom-web-model", provider="openai")

    assert llm.call_args.kwargs["model"] == "custom-web-model"
    assert thread.id in cog.chat_agents


@pytest.mark.asyncio
async def test_actual_native_agent_sends_hosted_and_local_tools_and_retains_followup_history(monkeypatch):
    from langchain_core.tools import Tool
    from services import agent_service, usage_service
    from services.openai_client_service import RequestOpenAI, RequestAsyncOpenAI
    from tests.test_responses_service import api_response

    monkeypatch.setenv("OPENAI_API_KEY", "offline-openai-key")
    monkeypatch.setattr(usage_service, "_tokenizer_unavailable", True)
    requests = []
    crawled = []

    def reply(request):
        assert request.url.path.endswith("/responses")
        assert request.headers["Authorization"] == "Bearer personal-openai-key"
        requests.append(json.loads(request.content))
        response = deepcopy(api_response())
        response["id"] = f"resp_internet_{len(requests)}"
        if len(requests) == 1:
            response["output"] = [
                {
                    "type": "web_search_call", "id": "ws_initial", "status": "completed",
                    "action": {"type": "search", "query": "latest version"},
                },
                {
                    "type": "function_call", "id": "fc_initial", "call_id": "call_initial",
                    "name": "Web-Crawling-Tool", "status": "completed",
                    "arguments": json.dumps({"__arg1": "https://example.org/release"}),
                },
            ]
        return httpx.Response(200, json=response)

    monkeypatch.setattr(
        agent_service, "_AgentOpenAI",
        lambda **kwargs: RequestOpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(reply))),
    )
    monkeypatch.setattr(
        agent_service, "_AgentAsyncOpenAI",
        lambda **kwargs: RequestAsyncOpenAI(**kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(reply))),
    )
    llm = agent_service.create_chat_llm(
        "gpt-6.1-sol", api_key="personal-openai-key",
        model_kwargs={"max_tool_calls": 2}, store=False,
        max_tokens=4096, output_version="responses/v1",
    )
    llm.disable_streaming = True
    def read_source(url):
        crawled.append(url)
        return "Offline source confirms this release is current."

    crawler = Tool(name="Web-Crawling-Tool", func=read_source, description="Read a source URL")
    agent = agent_service.create_chat_agent(
        llm, [crawler], "Use recent sources and cite them.",
        builtin_tools=[{"type": "web_search", "external_web_access": True}],
    )
    try:
        first = await asyncio.to_thread(agent.invoke, {"input": "What release changed today?"})
        second = await asyncio.to_thread(agent.invoke, {"input": "Which change matters for my prior question?"})
        assert len(requests) == 3
        assert crawled == ["https://example.org/release"]
        for request in requests:
            assert request["store"] is False
            assert request["max_tool_calls"] == 2
            assert request["max_output_tokens"] == 4096
            assert {tool["type"] for tool in request["tools"]} == {"web_search", "function"}
            assert any(tool.get("name") == "Web-Crawling-Tool" for tool in request["tools"])
            assert any(tool == {"type": "web_search", "external_web_access": True} for tool in request["tools"])
        scratchpad = json.dumps(requests[1]["input"])
        assert "function_call_output" in scratchpad
        assert "Offline source confirms this release is current." in scratchpad
        history = json.dumps(requests[2]["input"])
        assert "What release changed today?" in history
        assert "The release is current." in history
        assert "Which change matters for my prior question?" in history
        for result in (first, second):
            cited = agent_service.agent_search_response(result["output"])
            assert cited is not None
            assert "https://example.org/release" in cited.text
            assert cited.citations[0].url == "https://example.org/release"
    finally:
        llm.root_client.close()
        await llm.root_async_client.close()


def test_installed_search_stack_starts_native_chat_without_google_credentials(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = {
        **os.environ, "PYTHONPATH": str(root), "DISCORD_TOKEN": "offline-discord-token",
        "OPENAI_API_KEY": "offline-openai-key", "ALLOWED_GUILDS": "1",
        "DATA_DIR": str(tmp_path), "SHARE_DIR": str(tmp_path),
        "ENABLE_INDEXING": "false", "USER_INPUT_API_KEYS": "false", "HEALTH_SERVICE_ENABLED": "false",
    }
    for key in ("GOOGLE_SEARCH_API_KEY", "GOOGLE_SEARCH_ENGINE_ID", "PINECONE_TOKEN", "QDRANT_URL", "QDRANT_PATH", "QDRANT_API_KEY", "DEEPL_TOKEN", "E2B_API_KEY"):
        env.pop(key, None)
    code = """
import asyncio
import socket
from pycord.multicog import Bot
import gpt3discord

def forbid_network(*args, **kwargs):
    raise AssertionError("Startup attempted a network request")
socket.create_connection = forbid_network
async def fake_start(self, token, *args, **kwargs):
    assert token == "offline-discord-token"
    assert self.get_cog("SearchService") is not None
    assert self.get_cog("GPT3ComCon") is not None
    assert self.get_cog("WebSearchCog") is not None
    print("NATIVE_CHAT_ASSEMBLED")
Bot.start = fake_start
asyncio.run(gpt3discord.main())
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=tmp_path, env=env,
        text=True, capture_output=True, timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "NATIVE_CHAT_ASSEMBLED" in result.stdout


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", [0, 7])
async def test_invalid_scope_is_rejected_before_creating_native_thread(internet, monkeypatch, scope):
    llm, build, _ = capture_agent(internet, monkeypatch)
    cog = internet.SearchService(None, None, None, None, None)
    ctx, _, starter = command_context()

    await cog.search_chat_command(ctx, model="gpt-6.1-sol", search_scope=scope)

    ctx.respond.assert_awaited_once()
    assert ctx.respond.await_args.kwargs["ephemeral"] is True
    ctx.send.assert_not_awaited()
    starter.create_thread.assert_not_awaited()
    llm.assert_not_called()
    build.assert_not_called()


def native_output(text, url="https://example.org/release"):
    return [
        {
            "type": "web_search_call", "id": "ws_offline", "status": "completed",
            "action": {"type": "search", "query": "current release"},
        },
        {
            "type": "text", "text": text,
            "annotations": [{
                "type": "url_citation", "title": "Release notes", "url": url,
                "start_index": len(text) - 3, "end_index": len(text),
            }],
        },
    ]


def native_session(internet, monkeypatch, output):
    import discord

    monkeypatch.setattr(internet, "message_has_access", AsyncMock(return_value=True))
    usage = SimpleNamespace(update_usage_memory=Mock())
    cog = internet.SearchService(None, None, usage, None, None)
    agent = SimpleNamespace(invoke=Mock(return_value={
        "output": output,
        "intermediate_steps": [(SimpleNamespace(tool="Web-Crawling-Tool"), "source content")],
    }))
    cog.chat_agents[123] = agent
    message = SimpleNamespace(
        author=SimpleNamespace(id=42, bot=False),
        guild=SimpleNamespace(name="Test server"),
        channel=SimpleNamespace(id=123, trigger_typing=AsyncMock()),
        type=discord.MessageType.default, content="What changed today?",
        reply=AsyncMock(),
    )
    return cog, message, agent


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["The release is current. ", "Recent evidence remains available. " * 100])
async def test_native_chat_replies_with_clickable_citations_tool_footer_and_no_mentions(internet, monkeypatch, body):
    output = native_output(body + "[1]")
    cog, message, agent = native_session(internet, monkeypatch, output)

    await cog.on_message(message)

    agent.invoke.assert_called_once_with({"input": message.content})
    replies = message.reply.await_args_list
    assert 1 <= len(replies) <= 6
    descriptions = []
    for reply in replies:
        assert "file" not in reply.kwargs
        embed = reply.kwargs["embed"]
        descriptions.append(embed.description)
        assert len(embed.description) <= 1900
        assert embed.footer.text == "Used tools: OpenAI Web Search, Web Crawler"
        assert reply.kwargs["allowed_mentions"].to_dict() == {"parse": []}
    full = "\n".join(descriptions)
    assert "[[1]](<https://example.org/release>)" in full
    assert "[Release notes](<https://example.org/release>)" in full
    assert not cog.thread_awaiting_responses


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["long_citation", "over_six_chunks"])
async def test_native_chat_attaches_complete_answer_when_citations_or_output_cannot_fit(internet, monkeypatch, reason):
    from services.agent_service import agent_search_response
    from services.responses_service import discord_messages

    url = "https://example.org/" + ("source" * 400 if reason == "long_citation" else "release")
    body = "Complete current evidence. " * (1 if reason == "long_citation" else 600)
    output = native_output(body + "[1]", url)
    result = agent_search_response(output)
    if reason == "long_citation":
        with pytest.raises(ValueError, match="source link"):
            discord_messages(result, limit=1900)
    else:
        assert len(discord_messages(result, limit=1900)) > 6
    cog, message, _ = native_session(internet, monkeypatch, output)

    await cog.on_message(message)

    message.reply.assert_awaited_once()
    reply = message.reply.await_args
    assert "attached" in reply.args[0]
    assert reply.kwargs["allowed_mentions"].to_dict() == {"parse": []}
    attachment = reply.kwargs["file"]
    try:
        assert attachment.filename == "internet-chat.txt"
        content = attachment.fp.read().decode("utf-8")
        assert content.startswith(body)
        assert url in content
        assert "Release notes" in content
    finally:
        attachment.close()
    assert not cog.thread_awaiting_responses
