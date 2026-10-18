"""Document-chat workflows across commands, persisted indexes and real retrieval.

Only transport, embeddings and LLM decisions are simulated. The production add,
load, chat, attachment, PDF reader, persistence and query-engine paths run intact;
no test installs a retrieval tool into a chat.
"""

import asyncio
import importlib
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

pytest.importorskip("llama_index.core")
pytest.importorskip("llama_index.llms.openai")
pytest.importorskip("langchain_classic")
pytest.importorskip("langchain_openai")

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from llama_index.core.callbacks import CallbackManager, TokenCountingHandler
from llama_index.core.embeddings import MockEmbedding
from llama_index.core.llms import MockLLM

OWNER = 42
OTHER = 84
BOT = 999


def pdf_bytes(text):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 72 720 Td (" + text.encode("ascii") + b") Tj ET")
    page[NameObject("/Contents")] = stream
    result = io.BytesIO()
    writer.write(result)
    return result.getvalue()


class SourceContextLLM(MockLLM):
    model: str = "gpt-6-luna"
    api_key: str = "offline-key"

    def predict(self, prompt, **kwargs):
        # Answers can contain fixture facts only if real retrieval supplied them.
        return (
            kwargs.get("context_str")
            or kwargs.get("context_msg")
            or "No source context"
        )

    async def apredict(self, prompt, **kwargs):
        return self.predict(prompt, **kwargs)


class OfflineEmbedding(MockEmbedding):
    api_key: str = "offline-key"


class OfflineAgentLLM(FakeListChatModel):
    model_name: str = "gpt-6-luna"

    def get_num_tokens_from_messages(self, messages, **kwargs):
        return sum(len(str(message.content).encode()) for message in messages)


class RetrievalAgent:
    """Simulate agent decisions while invoking only production-created tools."""

    def __init__(self, tools, memory):
        self.tools, self.memory = list(tools), memory
        self.invocations = []

    def invoke(self, values):
        self.invocations.append(values)
        tools = [tool for tool in self.tools if "Dummy" not in tool.name]
        answers = [str(tool.invoke(values["input"])) for tool in tools]
        result = (
            "RETRIEVED_SOURCE: " + "\n".join(answers)
            if answers
            else "NO_CONNECTED_DOCUMENTS"
        )
        if self.memory is not None:
            self.memory.save_context(values, {"output": result})
        return {"output": result}


@pytest.fixture
def workflow(monkeypatch, tmp_path):
    import llama_index.core
    from services import indexing_service

    monkeypatch.setenv("ALLOWED_GUILDS", "1")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SHARE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "offline-key")
    monkeypatch.setenv("USER_INPUT_API_KEYS", "false")
    monkeypatch.delenv("BOT_ACCESS_ROLE_IDS", raising=False)
    monkeypatch.setattr(llama_index.core, "global_tokenizer", lambda text: text.split())
    model = importlib.import_module("models.index_model")
    cog_module = importlib.import_module("cogs.index_service_cog")
    monkeypatch.setattr(cog_module, "USER_INPUT_API_KEYS", False)
    monkeypatch.setattr(cog_module, "PRE_MODERATE", False)
    monkeypatch.setattr(cog_module, "message_has_access", AsyncMock(return_value=True))
    monkeypatch.setattr(model, "safe_ctx_respond", AsyncMock())
    monkeypatch.setattr(
        model,
        "index_tokenizer",
        lambda: SimpleNamespace(encode=lambda text: text.split()),
    )

    def context_factory(api_key=None, model=None, llm=None, embedding_model=None):
        if embedding_model not in {
            None,
            "text-embedding-3-small",
            "text-embedding-ada-002",
        }:
            raise ValueError("Unsupported embedding model in saved index")
        counter = TokenCountingHandler(tokenizer=lambda text: text.split())
        callbacks = CallbackManager([counter])
        return indexing_service.IndexContext(
            llm=SourceContextLLM(max_tokens=256, callback_manager=callbacks),
            embed_model=OfflineEmbedding(
                embed_dim=8,
                model_name="text-embedding-3-small",
                callback_manager=callbacks,
            ),
            callback_manager=callbacks,
            token_counter=counter,
        )

    monkeypatch.setattr(model, "create_index_context", context_factory)
    monkeypatch.setattr(indexing_service, "create_index_context", context_factory)
    monkeypatch.setattr(
        model,
        "create_chat_llm",
        lambda *args, **kwargs: OfflineAgentLLM(responses=["Offline agent"]),
    )
    agents = []

    def build_agent(llm, tools, system_message, memory=None, **kwargs):
        agent = RetrievalAgent(tools, memory)
        agents.append(agent)
        return agent

    monkeypatch.setattr(model, "create_chat_agent", build_agent)
    urls, downloads = {}, []

    class Response:
        def __init__(self, url):
            self.url = url
            self.status = 200 if url in urls else 404
            self.headers = {"Content-Type": "application/pdf"}
            self.content = self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def raise_for_status(self):
            if self.status != 200:
                raise ValueError("Fixture document unavailable")

        async def read(self):
            return urls[self.url]

        async def iter_chunked(self, size):
            data = urls[self.url]
            for offset in range(0, len(data), size):
                yield data[offset : offset + size]

    class Session:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def get(self, url, **kwargs):
            downloads.append(url)
            return Response(url)

    monkeypatch.setattr(model.aiohttp, "ClientSession", Session)
    usage = SimpleNamespace(
        update_usage=AsyncMock(),
        get_price=AsyncMock(return_value=0.001),
        update_usage_memory=Mock(),
    )
    bot = SimpleNamespace(user=SimpleNamespace(id=BOT))

    def cog():
        return cog_module.IndexService(bot, usage, asyncio.Queue())

    return SimpleNamespace(
        model=model,
        cog_module=cog_module,
        cog=cog,
        urls=urls,
        downloads=downloads,
        agents=agents,
        root=tmp_path,
        context=context_factory,
    )


def command_ctx(owner=OWNER, *, channel=None, thread_id=123):
    guild = SimpleNamespace(id=1, name="Test guild")
    thread = SimpleNamespace(
        id=thread_id,
        guild=guild,
        send=AsyncMock(),
        edit=AsyncMock(),
        trigger_typing=AsyncMock(),
    )
    progress = SimpleNamespace(edit=AsyncMock(), delete=AsyncMock())
    channel = channel or SimpleNamespace(
        id=2, guild=guild, send=AsyncMock(return_value=progress)
    )
    starter = SimpleNamespace(create_thread=AsyncMock(return_value=thread))
    return SimpleNamespace(
        user=SimpleNamespace(id=owner, name=f"User {owner}"),
        guild=guild,
        guild_id=1,
        interaction=SimpleNamespace(guild_id=1),
        channel=channel,
        defer=AsyncMock(),
        respond=AsyncMock(return_value=progress),
        send=AsyncMock(return_value=starter),
        thread=thread,
        starter=starter,
    )


def attachment(name, text):
    return SimpleNamespace(
        filename=name,
        content_type="text/plain",
        size=len(text),
        read=AsyncMock(return_value=text.encode()),
    )


async def add_pdf(workflow, cog, text, *, name="resume", owner=OWNER, channel=None):
    url = f"https://example.invalid/{name}.pdf"
    workflow.urls[url] = pdf_bytes(text)
    ctx = command_ctx(owner, channel=channel)
    await cog.set_index_command(ctx, link=url)
    paths = list((workflow.root / "indexes" / str(owner)).glob("*/docstore.json"))
    return ctx, paths


async def start_chat(cog, owner=OWNER, *, name=None, thread_id=123):
    ctx = command_ctx(owner, thread_id=thread_id)
    kwargs = {"user_index": name} if name is not None else {}
    await cog.index_chat_command(ctx, "gpt-6-luna", 0, 1, **kwargs)
    return ctx


async def ask(
    cog,
    thread,
    text="What details do the connected documents contain?",
    *,
    owner=OWNER,
    attachments=(),
):
    reply = SimpleNamespace(delete=AsyncMock())
    message = SimpleNamespace(
        author=SimpleNamespace(id=owner, name=f"User {owner}", bot=False),
        guild=thread.guild,
        channel=thread,
        type=discord.MessageType.default,
        content=text,
        attachments=list(attachments),
        reply=AsyncMock(return_value=reply),
        delete=AsyncMock(),
    )
    await cog.on_message(message)
    answers = [
        call.kwargs["embed"].description
        for call in message.reply.await_args_list
        if "embed" in call.kwargs
        and (call.kwargs["embed"].description or "").startswith("RETRIEVED_SOURCE:")
    ]
    return "\n".join(answers), message


@pytest.mark.asyncio
async def test_add_pdf_then_chat_uses_real_saved_document_retrieval(workflow):
    cog = workflow.cog()
    _, paths = await add_pdf(workflow, cog, "Resume verification codename FENNEL7319")
    assert len(paths) == 1 and "FENNEL7319" in paths[0].read_text()
    assert workflow.downloads.count("https://example.invalid/resume.pdf") == 2

    ctx = await start_chat(cog)
    answer, _ = await ask(cog, ctx.thread)

    assert "FENNEL7319" in answer
    assert "NO_CONNECTED_DOCUMENTS" not in answer


@pytest.mark.asyncio
async def test_restart_chat_restores_latest_own_saved_index_and_excludes_other_owner(
    workflow,
):
    original = workflow.cog()
    await add_pdf(workflow, original, "First project codename CARAWAY812", name="older")
    await add_pdf(
        workflow, original, "Current project codename MARJORAM937", name="latest"
    )
    await add_pdf(
        workflow, original, "Other owner secret ROSEMARY281", owner=OTHER, name="other"
    )
    # Sort explicitly: filesystem/glob order is unrelated to selection recency.
    for path in (workflow.root / "indexes" / str(OWNER)).iterdir():
        stamp = 200 if "latest" in path.name else 100
        for item in [*path.iterdir(), path]:
            os.utime(item, (stamp, stamp))
    restarted = workflow.cog()

    ctx = await start_chat(restarted)
    answer, _ = await ask(restarted, ctx.thread)

    assert "MARJORAM937" in answer
    assert "CARAWAY812" not in answer
    assert "ROSEMARY281" not in answer


def saved_name(workflow, fragment, owner=OWNER):
    return next(
        path.name
        for path in (workflow.root / "indexes" / str(owner)).iterdir()
        if fragment in path.name
    )


def all_replies(message):
    return "\n".join(
        (call.kwargs["embed"].description or "")
        if "embed" in call.kwargs
        else str(call.args[0])
        if call.args
        else ""
        for call in message.reply.await_args_list
    )


@pytest.mark.asyncio
async def test_explicit_saved_index_selection_wins_over_last_added_index(workflow):
    cog = workflow.cog()
    await add_pdf(workflow, cog, "Selected resume marker ANISE536", name="selected")
    await add_pdf(workflow, cog, "Unselected finance marker BORAGE468", name="other")

    ctx = await start_chat(cog, name=saved_name(workflow, "selected"))
    answer, _ = await ask(cog, ctx.thread)

    assert "ANISE536" in answer
    assert "BORAGE468" not in answer
    assert saved_name(workflow, "selected") in str(
        ctx.send.await_args.kwargs["embed"].to_dict()
    )


@pytest.mark.asyncio
async def test_load_saved_selection_then_chat_uses_loaded_source_after_restart(
    workflow,
):
    original = workflow.cog()
    await add_pdf(workflow, original, "Earlier resume marker BASIL193", name="chosen")
    await add_pdf(
        workflow, original, "Latest unrelated marker CHERVIL274", name="unrelated"
    )
    restarted = workflow.cog()
    selected = saved_name(workflow, "chosen")

    await restarted.load_index_command(command_ctx(), selected, None, None)
    ctx = await start_chat(restarted)
    answer, _ = await ask(restarted, ctx.thread)

    assert "BASIL193" in answer
    assert "CHERVIL274" not in answer


@pytest.mark.asyncio
async def test_add_inside_owned_chat_connects_new_source_and_keeps_existing_tools_and_memory(
    workflow,
):
    cog = workflow.cog()
    await add_pdf(workflow, cog, "Original source marker DILL815", name="original")
    ctx = await start_chat(cog)
    await ask(cog, ctx.thread)
    original_memory = cog.index_handler.index_chat_chains[ctx.thread.id].memory

    await add_pdf(
        workflow,
        cog,
        "Additional source marker SORREL328",
        name="additional",
        channel=ctx.thread,
    )
    answer, _ = await ask(cog, ctx.thread)

    assert "DILL815" in answer and "SORREL328" in answer
    assert cog.index_handler.index_chat_chains[ctx.thread.id].memory is original_memory
    assert len(original_memory.chat_memory.messages) >= 4


@pytest.mark.asyncio
async def test_load_inside_owned_chat_connects_selected_source_and_preserves_prior_source(
    workflow,
):
    cog = workflow.cog()
    await add_pdf(workflow, cog, "Archived project marker LOVAGE617", name="archived")
    await add_pdf(workflow, cog, "Current project marker SAVORY459", name="current")
    ctx = await start_chat(cog)
    original_memory = cog.index_handler.index_chat_chains[ctx.thread.id].memory

    await cog.load_index_command(
        command_ctx(channel=ctx.thread), saved_name(workflow, "archived"), None, None
    )
    answer, _ = await ask(cog, ctx.thread)

    assert "LOVAGE617" in answer and "SAVORY459" in answer
    assert cog.index_handler.index_chat_chains[ctx.thread.id].memory is original_memory


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["add", "load"])
async def test_other_owners_commands_cannot_attach_their_documents_to_an_existing_chat(
    workflow, operation
):
    cog = workflow.cog()
    await add_pdf(workflow, cog, "Thread owner marker TARRAGON126", name="owner")
    await add_pdf(
        workflow,
        cog,
        "Private other owner marker CILANTRO584",
        name="private",
        owner=OTHER,
    )
    ctx = await start_chat(cog)
    old_tools = list(cog.index_handler.index_chat_chains[ctx.thread.id].tools)

    if operation == "add":
        await add_pdf(
            workflow,
            cog,
            "Other uploaded marker PAPRIKA963",
            name="foreign",
            owner=OTHER,
            channel=ctx.thread,
        )
    else:
        await cog.load_index_command(
            command_ctx(OTHER, channel=ctx.thread),
            saved_name(workflow, "private", OTHER),
            None,
            None,
        )
    answer, _ = await ask(cog, ctx.thread)

    assert "TARRAGON126" in answer
    assert "CILANTRO584" not in answer and "PAPRIKA963" not in answer
    assert cog.index_handler.index_chat_chains[ctx.thread.id].tools == old_tools


@pytest.mark.asyncio
async def test_two_in_chat_attachments_with_colliding_sanitized_names_remain_queryable(
    workflow,
):
    cog = workflow.cog()
    ctx = await start_chat(cog)

    await ask(
        cog,
        ctx.thread,
        attachments=[attachment("report-a.txt", "First attached marker PARSNIP291")],
    )
    await ask(
        cog,
        ctx.thread,
        attachments=[attachment("report_a.txt", "Second attached marker CLOVER743")],
    )
    answer, _ = await ask(cog, ctx.thread)

    assert "PARSNIP291" in answer and "CLOVER743" in answer
    tools = [
        tool
        for tool in cog.index_handler.index_chat_chains[ctx.thread.id].tools
        if "Dummy" not in tool.name
    ]
    assert len(tools) == 2
    assert len({tool.name for tool in tools}) == 2
    assert all(tool.name.isascii() and len(tool.name) <= 64 for tool in tools)


@pytest.mark.asyncio
async def test_empty_chat_gives_guidance_without_agent_request_then_accepts_an_attachment(
    workflow,
):
    cog = workflow.cog()
    ctx = await start_chat(cog)
    initial_agent = cog.index_handler.index_chat_chains[ctx.thread.id].agent_chain

    _, message = await ask(cog, ctx.thread)

    assert not initial_agent.invocations
    guidance = all_replies(message).lower()
    assert any(
        word in guidance for word in ("attach", "upload", "/index add", "/index load")
    )
    assert "no" in guidance
    answer, _ = await ask(
        cog,
        ctx.thread,
        attachments=[attachment("new.txt", "First usable document marker CARDAMOM591")],
    )
    assert "CARDAMOM591" in answer


@pytest.mark.asyncio
async def test_failed_pdf_add_preserves_current_selection_and_chat_data(workflow):
    cog = workflow.cog()
    await add_pdf(workflow, cog, "Usable retained marker JUNIPER357", name="retained")
    previous = cog.index_handler.index_storage[OWNER].queryable_index
    ctx = await start_chat(cog)
    workflow.urls["https://example.invalid/broken.pdf"] = b"not a PDF"

    await cog.set_index_command(
        command_ctx(channel=ctx.thread), link="https://example.invalid/broken.pdf"
    )
    answer, _ = await ask(cog, ctx.thread)

    assert cog.index_handler.index_storage[OWNER].queryable_index is previous
    assert "JUNIPER357" in answer
    assert not any(
        "broken" in path.name
        for path in (workflow.root / "indexes" / str(OWNER)).iterdir()
    )


@pytest.mark.asyncio
async def test_blank_pdf_does_not_become_a_successful_empty_index(workflow):
    cog = workflow.cog()
    await add_pdf(workflow, cog, "Retained nonempty marker SUMAC462", name="retained")
    previous = cog.index_handler.index_storage[OWNER].queryable_index

    ctx, _ = await add_pdf(workflow, cog, "", name="blank")

    assert cog.index_handler.index_storage[OWNER].queryable_index is previous
    assert not any(
        "blank" in path.name
        for path in (workflow.root / "indexes" / str(OWNER)).iterdir()
    )
    rendered = str(
        ctx.respond.return_value.edit.await_args.kwargs["embed"].to_dict()
    ).lower()
    assert any(word in rendered for word in ("text", "empty", "content"))


@pytest.mark.asyncio
async def test_failed_persistence_does_not_select_or_attach_uncommitted_document(
    workflow, monkeypatch
):
    cog = workflow.cog()
    await add_pdf(
        workflow, cog, "Committed source marker PEPPERCORN852", name="committed"
    )
    previous = cog.index_handler.index_storage[OWNER].queryable_index
    ctx = await start_chat(cog)
    monkeypatch.setattr(
        workflow.model, "persist_index", Mock(side_effect=OSError("Disk full"))
    )

    await add_pdf(
        workflow,
        cog,
        "Uncommitted source marker SAFFRON794",
        name="uncommitted",
        channel=ctx.thread,
    )
    answer, _ = await ask(cog, ctx.thread)

    assert cog.index_handler.index_storage[OWNER].queryable_index is previous
    assert "PEPPERCORN852" in answer
    assert "SAFFRON794" not in answer


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["../84/private", r"nested\private"])
async def test_explicit_saved_selection_cannot_escape_owners_directory(workflow, name):
    cog = workflow.cog()
    await add_pdf(
        workflow,
        cog,
        "Other owner protected marker NUTMEG718",
        owner=OTHER,
        name="private",
    )

    ctx = await start_chat(cog, name=name)

    ctx.starter.create_thread.assert_not_awaited()
    assert not cog.index_handler.index_chat_chains


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["corrupt_storage", "incompatible_manifest"])
async def test_invalid_saved_selection_is_reported_without_starting_a_misleading_chat(
    workflow, failure
):
    cog = workflow.cog()
    await add_pdf(workflow, cog, "Valid saved marker TURMERIC247", name="selected")
    selected = saved_name(workflow, "selected")
    path = workflow.root / "indexes" / str(OWNER) / selected
    if failure == "corrupt_storage":
        (path / "docstore.json").write_text("invalid JSON")
    else:
        (path / "gptdiscord-index.json").write_text(
            json.dumps({"embedding_model": "unsupported-model"})
        )
    fresh = workflow.cog()

    ctx = await start_chat(fresh, name=selected)

    ctx.starter.create_thread.assert_not_awaited()
    assert not fresh.index_handler.index_chat_chains


@pytest.mark.asyncio
@pytest.mark.parametrize("deep", [False, True])
async def test_composed_active_index_connects_multiple_saved_sources_to_chat(
    workflow, deep
):
    cog = workflow.cog()
    await add_pdf(workflow, cog, "First component marker ALLSPICE862", name="first")
    await add_pdf(workflow, cog, "Second component marker CHICORY415", name="second")

    await cog.index_handler.compose_indexes(
        OWNER,
        [saved_name(workflow, "first"), saved_name(workflow, "second")],
        "combined",
        deep,
        workflow.context(),
    )
    ctx = await start_chat(cog)
    answer, _ = await ask(cog, ctx.thread)

    assert "ALLSPICE862" in answer and "CHICORY415" in answer


@pytest.mark.asyncio
@pytest.mark.parametrize("updated_file", ["index_store.json", "gptdiscord-index.json"])
async def test_restart_latest_selection_recognizes_overwritten_saved_index_metadata(
    workflow, updated_file
):
    original = workflow.cog()
    await add_pdf(workflow, original, "Original resume marker CURRY461", name="resume")
    await add_pdf(workflow, original, "Newer folder marker WASABI837", name="newer")
    await add_pdf(
        workflow, original, "Updated resume marker GALANGAL592", name="resume"
    )
    resume_name = saved_name(workflow, "resume")
    resume = workflow.root / "indexes" / str(OWNER) / resume_name
    newer = workflow.root / "indexes" / str(OWNER) / saved_name(workflow, "newer")
    for directory, stamp in ((resume, 100), (newer, 200)):
        for path in [*directory.iterdir(), directory]:
            os.utime(path, (stamp, stamp))
    os.utime(resume / updated_file, (300, 300))
    assert resume.stat().st_mtime < newer.stat().st_mtime
    restarted = workflow.cog()

    ctx = await start_chat(restarted)
    answer, _ = await ask(restarted, ctx.thread)

    assert "GALANGAL592" in answer
    assert "CURRY461" not in answer and "WASABI837" not in answer
    assert restarted.index_handler.index_storage[OWNER].queryable_name == resume_name


@pytest.mark.asyncio
async def test_readding_and_reloading_saved_source_replaces_stale_tool_but_keeps_other_documents(
    workflow,
):
    cog = workflow.cog()
    await add_pdf(workflow, cog, "Old source marker GINGER193", name="resume")
    await add_pdf(
        workflow, cog, "Other connected source marker HORSERADISH627", name="other"
    )
    ctx = await start_chat(cog)
    selected = saved_name(workflow, "resume")
    await cog.load_index_command(command_ctx(channel=ctx.thread), selected, None, None)
    chat = cog.index_handler.index_chat_chains[ctx.thread.id]
    memory = chat.memory
    before, _ = await ask(cog, ctx.thread)
    assert "GINGER193" in before and "HORSERADISH627" in before

    await add_pdf(
        workflow,
        cog,
        "Updated source marker CUMIN845",
        name="resume",
        channel=ctx.thread,
    )
    after_add, _ = await ask(cog, ctx.thread)
    assert len(chat.tools) == 2
    assert "CUMIN845" in after_add and "HORSERADISH627" in after_add
    assert "GINGER193" not in after_add

    await cog.load_index_command(command_ctx(channel=ctx.thread), selected, None, None)
    after_load, _ = await ask(cog, ctx.thread)

    assert len(chat.tools) == 2
    assert len({tool.name for tool in chat.tools}) == 2
    assert chat.memory is memory
    assert "CUMIN845" in after_load and "HORSERADISH627" in after_load
    assert "GINGER193" not in after_load


@pytest.mark.asyncio
async def test_index_add_finishing_after_end_saves_without_mutating_or_reconnecting_dead_chat(
    workflow, monkeypatch
):
    cog = workflow.cog()
    await add_pdf(workflow, cog, "Original chat marker SESAME274", name="original")
    ctx = await start_chat(cog)
    chat = cog.index_handler.index_chat_chains[ctx.thread.id]
    old_tools, old_agent, old_memory = list(chat.tools), chat.agent_chain, chat.memory
    agent_count = len(workflow.agents)
    entered, release = asyncio.Event(), asyncio.Event()
    original_index_link = cog.index_handler.index_link

    async def blocked_index_link(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original_index_link(*args, **kwargs)

    monkeypatch.setattr(cog.index_handler, "index_link", blocked_index_link)
    url = "https://example.invalid/inflight.pdf"
    workflow.urls[url] = pdf_bytes("Completed document marker MUSTARD683")
    add_ctx = command_ctx(channel=ctx.thread)
    adding = asyncio.create_task(cog.set_index_command(add_ctx, link=url))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        end_message = SimpleNamespace(channel=ctx.thread, reply=AsyncMock())
        await cog.index_handler.execute_index_chat_message(end_message, "end")
        assert ctx.thread.id not in cog.index_handler.index_chat_chains
        release.set()
        await asyncio.wait_for(adding, 5)
    finally:
        release.set()
        if not adding.done():
            adding.cancel()
        await asyncio.gather(adding, return_exceptions=True)

    assert not cog.index_handler.index_chat_chains
    assert chat.tools == old_tools
    assert chat.agent_chain is old_agent and chat.memory is old_memory
    assert len(workflow.agents) == agent_count
    source = saved_name(workflow, "inflight")
    assert cog.index_handler.index_storage[OWNER].queryable_name == source
    success = str(
        add_ctx.respond.return_value.edit.await_args.kwargs["embed"].to_dict()
    )
    assert "Connected to this chat." not in success
    assert "next /index chat" in success

    next_ctx = await start_chat(cog, thread_id=456)
    answer, _ = await ask(cog, next_ctx.thread)

    assert "MUSTARD683" in answer
    assert "SESAME274" not in answer
