"""Offline checks for optional search/crawling and replay behavior."""

import importlib
import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("llama_index.core")
pytest.importorskip("llama_index.llms.openai")
pytest.importorskip("langchain_openai")
pytest.importorskip("langchain_classic")
pytest.importorskip("langchain_community")

from llama_index.core import Document
from llama_index.core.embeddings import MockEmbedding
from llama_index.core.llms import MockLLM
from llama_index.core.node_parser import SentenceSplitter


@pytest.fixture
def search_module(monkeypatch, tmp_path):
    monkeypatch.setenv("SHARE_DIR", str(tmp_path))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    module = importlib.import_module("cogs.search_service_cog")
    monkeypatch.setattr(module, "USER_INPUT_API_KEYS", False)
    monkeypatch.setattr(module, "PRE_MODERATE", False)
    monkeypatch.setattr(module, "WOLFRAM_API_KEY", None)
    return module


def test_crawler_uses_model_tokenizer_fallback_and_keeps_url_commas(
    search_module, monkeypatch
):
    module = search_module
    requested_urls = []

    def read_page(url):
        requested_urls.append(url)
        return [Document(text="Readable page content.")]

    monkeypatch.setattr(module, "read_web_documents", read_page)
    monkeypatch.setattr(
        module, "index_tokenizer",
        lambda: SimpleNamespace(encode=lambda text: text.split()),
    )

    wrapper = module.CustomTextRequestWrapper()
    assert wrapper.get("https://example.com/report,a,b") == "Readable page content."
    assert requested_urls == ["https://example.com/report,a,b"]
    assert "invalid URL" in wrapper.get("file:///etc/passwd")
    assert len(requested_urls) == 1


def test_long_page_summarization_uses_current_llama_index_context(
    search_module, monkeypatch
):
    module = search_module
    import llama_index.core

    monkeypatch.setattr(llama_index.core, "global_tokenizer", lambda text: text.split())
    monkeypatch.setattr(
        module,
        "index_tokenizer",
        lambda: SimpleNamespace(encode=list),
    )
    monkeypatch.setattr(module.Models, "get_max_tokens", lambda model: 1010)
    monkeypatch.setattr(
        module,
        "read_web_documents",
        lambda url: [
            Document(
                text="A source page about current technology and software releases."
            )
        ],
    )
    embedding = MockEmbedding(embed_dim=8)
    llm = MockLLM(max_tokens=4)
    context_calls = []

    def create_context(*, api_key, model):
        context_calls.append((api_key, model))
        return SimpleNamespace(
            embed_model=embedding,
            index_kwargs={
                "llm": llm,
                "embed_model": embedding,
                "transformations": [SentenceSplitter(chunk_size=128, chunk_overlap=0)],
            },
            query_kwargs={"llm": llm},
        )

    monkeypatch.setattr(module, "create_index_context", create_context)
    wrapper = module.CustomTextRequestWrapper(
        model=module.Models.DEFAULT, api_key="user-test-key"
    )
    result = wrapper.get(
        f"https://example.com, {module.Models.DEFAULT}, What changed, and when?"
    )
    assert result == "text text text text"
    assert context_calls == [("user-test-key", module.Models.DEFAULT)]


@pytest.mark.asyncio
async def test_redo_preserves_selected_model_and_search_modes(search_module):
    module = search_module
    ctx = SimpleNamespace(user=SimpleNamespace(id=123))
    redo = module.RedoSearchUser(
        ctx,
        "latest release",
        4,
        6,
        "compact",
        "gpt-6-luna",
        deep=True,
        multistep=True,
    )
    cog = SimpleNamespace(bot=None, redo_users={123: redo}, search_command=AsyncMock())
    button = module.RedoButton(ctx, cog)
    interaction = SimpleNamespace(
        _state=None, user=ctx.user,
        response=SimpleNamespace(send_message=AsyncMock()),
    )

    await button.callback(interaction)

    new_ctx = cog.search_command.await_args.args[0]
    assert new_ctx.interaction is interaction
    cog.search_command.assert_awaited_once_with(
        new_ctx,
        "latest release",
        4,
        6,
        deep=True,
        redo=True,
        response_mode="compact",
        model="gpt-6-luna",
        multistep=True,
    )


@pytest.mark.asyncio
async def test_search_chat_uses_structured_tool_observations(
    search_module, monkeypatch
):
    module = search_module
    captured = {}
    memory = SimpleNamespace()
    agent = SimpleNamespace(
        memory=memory,
        invoke=lambda inputs: {
            "output": [{"type": "text", "text": "Current answer with a source."}],
            "intermediate_steps": [
                (SimpleNamespace(tool="Search-Tool"), "search result")
            ],
        },
    )

    def create_agent(llm, tools, system_message, **kwargs):
        captured["tools"] = tools
        return agent

    monkeypatch.setattr(
        module,
        "GoogleSearchAPIWrapper",
        lambda **kwargs: SimpleNamespace(run=lambda query: "results"),
    )
    monkeypatch.setattr(module, "create_chat_llm", lambda **kwargs: object())
    monkeypatch.setattr(module, "create_chat_agent", create_agent)
    monkeypatch.setattr(module, "safe_ctx_respond", AsyncMock())
    thread = SimpleNamespace(id=321)
    sent_message = SimpleNamespace(create_thread=AsyncMock(return_value=thread))
    ctx = SimpleNamespace(
        user=SimpleNamespace(id=456, name="Dev"),
        defer=AsyncMock(),
        send=AsyncMock(return_value=sent_message),
    )
    usage = SimpleNamespace(update_usage_memory=lambda *args: None)
    cog = module.SearchService(
        SimpleNamespace(user=SimpleNamespace(id=999)), None, usage, None, None
    )
    monkeypatch.setattr(module, "GOOGLE_API_KEY", "offline-google")
    monkeypatch.setattr(module, "GOOGLE_SEARCH_ENGINE_ID", "offline-engine")
    await cog.search_chat_command(ctx, "gpt-6-luna", provider="google")
    message = SimpleNamespace(
        author=SimpleNamespace(id=123),
        guild=SimpleNamespace(name="Test server"),
        channel=SimpleNamespace(id=321, trigger_typing=AsyncMock()),
        type=module.discord.MessageType.default,
        content="What changed today?",
        reply=AsyncMock(),
    )

    await cog.on_message(message)

    reply = message.reply.await_args.kwargs["embed"]
    assert reply.description == "Current answer with a source."
    assert reply.footer.text == "Used tools: Google Search"
    assert len(captured["tools"]) == 2
    assert not cog.thread_awaiting_responses
    assert cog.thread_owners[thread.id] == 456


@pytest.mark.asyncio
async def test_listener_ignores_non_session_messages_before_moderation(
    search_module, monkeypatch
):
    module = search_module
    moderate = AsyncMock()
    monkeypatch.setitem(
        sys.modules,
        "services.moderations_service",
        SimpleNamespace(
            Moderation=SimpleNamespace(simple_moderate_and_respond=moderate),
        ),
    )
    monkeypatch.setattr(module, "PRE_MODERATE", True)
    cog = module.SearchService(
        SimpleNamespace(user=SimpleNamespace(id=999)), None, None, None, None
    )
    message = SimpleNamespace(
        author=SimpleNamespace(id=123),
        guild=object(),
        channel=SimpleNamespace(id=321),
        type=module.discord.MessageType.default,
        content="An unrelated server message",
        reply=AsyncMock(),
        delete=AsyncMock(),
    )
    cog.thread_awaiting_responses.append(321)

    await cog.on_message(message)

    moderate.assert_not_awaited()
    message.reply.assert_not_awaited()
    message.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_personal_key_session_rejects_other_participants_before_tools(
    search_module, monkeypatch
):
    module = search_module
    monkeypatch.setattr(module, "USER_INPUT_API_KEYS", True)
    monkeypatch.setattr(module, "PRE_MODERATE", True)
    moderate = AsyncMock()
    monkeypatch.setitem(
        sys.modules,
        "services.moderations_service",
        SimpleNamespace(
            Moderation=SimpleNamespace(simple_moderate_and_respond=moderate),
        ),
    )
    cog = module.SearchService(
        SimpleNamespace(user=SimpleNamespace(id=999)), None, None, None, None
    )
    agent = SimpleNamespace(invoke=Mock())
    cog.chat_agents[321] = agent
    cog.thread_owners[321] = 456
    message = SimpleNamespace(
        author=SimpleNamespace(id=789),
        guild=object(),
        channel=SimpleNamespace(id=321),
        type=module.discord.MessageType.default,
        content="Search for me",
        reply=AsyncMock(),
        delete=AsyncMock(),
    )

    await cog.on_message(message)

    message.reply.assert_awaited_once()
    assert "creator's API key" in message.reply.await_args.args[0]
    agent.invoke.assert_not_called()
    moderate.assert_not_awaited()


@pytest.mark.asyncio
async def test_premoderation_reserves_search_session_before_a_second_generation(
    search_module, monkeypatch
):
    module = search_module
    monkeypatch.setattr(module, "message_has_access", AsyncMock(return_value=True))
    monkeypatch.setattr(module, "PRE_MODERATE", True)
    entered, release = asyncio.Event(), asyncio.Event()

    async def premoderate(*args):
        entered.set()
        await release.wait()
        return False

    moderate = AsyncMock(side_effect=premoderate)
    monkeypatch.setitem(
        sys.modules,
        "services.moderations_service",
        SimpleNamespace(
            Moderation=SimpleNamespace(simple_moderate_and_respond=moderate),
        ),
    )
    cog = module.SearchService(
        SimpleNamespace(user=SimpleNamespace(id=999)),
        None,
        SimpleNamespace(update_usage_memory=lambda *args: None),
        None,
        SimpleNamespace(deletion_queue=asyncio.Queue()),
    )
    agent = SimpleNamespace(invoke=Mock(return_value={"output": "Answer"}))
    cog.chat_agents[321] = agent

    def message():
        return SimpleNamespace(
            author=SimpleNamespace(id=123, bot=False),
            guild=SimpleNamespace(name="Test"),
            channel=SimpleNamespace(id=321, trigger_typing=AsyncMock()),
            type=module.discord.MessageType.reply,
            content="What changed today?",
            reply=AsyncMock(),
        )

    first, second = message(), message()
    task = asyncio.create_task(cog.on_message(first))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await cog.on_message(second)
        assert "wait" in second.reply.await_args.args[0].lower()
        moderate.assert_awaited_once()
        agent.invoke.assert_not_called()
    finally:
        release.set()
        await asyncio.wait_for(task, 1)
    agent.invoke.assert_called_once()
    assert cog.thread_awaiting_responses == []


@pytest.mark.asyncio
async def test_search_reply_failure_releases_session(search_module, monkeypatch):
    module = search_module
    monkeypatch.setattr(module, "message_has_access", AsyncMock(return_value=True))
    cog = module.SearchService(
        SimpleNamespace(user=SimpleNamespace(id=999)),
        None,
        SimpleNamespace(update_usage_memory=lambda *args: None),
        None,
        None,
    )
    cog.chat_agents[321] = SimpleNamespace(invoke=lambda *args: {"output": "Answer"})
    message = SimpleNamespace(
        author=SimpleNamespace(id=123, bot=False),
        guild=SimpleNamespace(name="Test"),
        channel=SimpleNamespace(id=321, trigger_typing=AsyncMock()),
        type=module.discord.MessageType.default,
        content="What changed today?",
        reply=AsyncMock(side_effect=RuntimeError("Discord unavailable")),
    )

    with pytest.raises(RuntimeError, match="Discord unavailable"):
        await cog.on_message(message)
    assert cog.thread_awaiting_responses == []


@pytest.mark.asyncio
@pytest.mark.parametrize("output", ["", [{"type": "reasoning", "summary": []}]])
async def test_empty_model_answer_gives_guidance_and_releases_session(search_module, monkeypatch, output):
    module = search_module
    monkeypatch.setattr(module, "message_has_access", AsyncMock(return_value=True))
    cog = module.SearchService(
        None, None, SimpleNamespace(update_usage_memory=lambda *args: None), None, None,
    )
    cog.chat_agents[321] = SimpleNamespace(invoke=lambda *args: {"output": output})
    message = SimpleNamespace(
        author=SimpleNamespace(id=123, bot=False), guild=SimpleNamespace(name="Test"),
        channel=SimpleNamespace(id=321, trigger_typing=AsyncMock()),
        type=module.discord.MessageType.default, content="Search now", reply=AsyncMock(),
    )
    await cog.on_message(message)
    assert "no text answer" in message.reply.await_args.args[0]
    assert "embed" not in message.reply.await_args.kwargs
    assert cog.thread_awaiting_responses == []
