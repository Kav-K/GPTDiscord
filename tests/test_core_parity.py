"""Additional original-feature regressions found in the parity audit."""

import importlib
import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest


def requester(user_id=42):
    return SimpleNamespace(
        id=user_id,
        name="Tester",
        discriminator="0",
        avatar=None,
        default_avatar=SimpleNamespace(url="https://example.invalid/avatar.png"),
    )


@pytest.fixture
def search(monkeypatch, tmp_path):
    pytest.importorskip("llama_index.core")
    pytest.importorskip("langchain_classic")
    pytest.importorskip("langchain_openai")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SHARE_DIR", str(tmp_path))
    module = importlib.import_module("cogs.search_service_cog")
    monkeypatch.setattr(module, "USER_INPUT_API_KEYS", False)
    monkeypatch.setattr(module, "PRE_MODERATE", False)
    return module


def search_result(module):
    ctx = SimpleNamespace(user=requester(), interaction=object())
    request = module.RedoSearchUser(
        ctx, "Original query", 4, 6, "compact", "gpt-6-luna", deep=True, multistep=True
    )
    cog = SimpleNamespace(
        bot=None, redo_users={42: request}, search_command=AsyncMock()
    )
    return ctx, request, cog


def component_interaction(user_id=42):
    return SimpleNamespace(
        _state=None,
        user=requester(user_id),
        response=SimpleNamespace(
            send_message=AsyncMock(), send_modal=AsyncMock(), defer=AsyncMock()
        ),
        message=SimpleNamespace(jump_url="https://discord.com/channels/1/2/3"),
    )


@pytest.mark.asyncio
async def test_old_search_controls_keep_their_request_and_use_current_interaction(search):
    ctx, request, cog = search_result(search)
    button = search.RedoButton(ctx, cog)
    cog.redo_users[42] = search.RedoSearchUser(ctx, "New query", 1, 1, "refine", "gpt-6-sol")
    interaction = component_interaction()

    await button.callback(interaction)

    args, kwargs = cog.search_command.await_args
    assert isinstance(args[0], discord.ApplicationContext)
    assert args[0].interaction is interaction
    assert args[0].user.id == 42
    assert args[1:] == (request.query, request.search_scope, request.nodes)
    assert kwargs == dict(
        deep=True, redo=True, response_mode="compact", model="gpt-6-luna", multistep=True
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("control", ["redo", "followup", "modal"])
async def test_search_controls_reject_another_person_before_any_provider_work(search, control):
    ctx, request, cog = search_result(search)
    text = "**Final Answer:**Original answer**Sources:**https://example.invalid"
    item = {
        "redo": lambda: search.RedoButton(ctx, cog),
        "followup": lambda: search.FollowupButton(ctx, cog, text),
        "modal": lambda: search.FollowupModal(ctx, cog, text),
    }[control]()
    interaction = component_interaction(43)

    await item.callback(interaction)

    cog.search_command.assert_not_awaited()
    interaction.response.send_modal.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.await_args.kwargs["ephemeral"]
    if control == "modal":
        item.stop()


@pytest.mark.asyncio
async def test_search_followup_works_without_message_on_modal_submission(search):
    ctx, request, cog = search_result(search)
    text = "**Final Answer:**Original answer**Sources:**https://example.invalid"
    button = search.FollowupButton(ctx, cog, text)
    interaction = component_interaction()
    await button.callback(interaction)
    modal = interaction.response.send_modal.await_args.kwargs["modal"]
    cog.redo_users[42] = search.RedoSearchUser(ctx, "New query", 1, 1, "refine", "gpt-6-sol")
    modal.children[0].value = "What changed?"
    submission = component_interaction()
    submission.message = None

    await modal.callback(submission)

    args, kwargs = cog.search_command.await_args
    assert args[0].interaction is submission
    assert "Original query" in args[1] and "Original answer" in args[1]
    assert "New query" not in args[1]
    assert kwargs["from_followup"].original_link == interaction.message.jump_url
    assert kwargs["model"] == "gpt-6-luna"
    modal.stop()


@pytest.mark.asyncio
async def test_google_search_fetches_second_page_for_sixteen_sources(search, monkeypatch):
    module = importlib.import_module("models.search_model")
    calls = []

    class Response:
        def __init__(self, start):
            self.start = start
            self.status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def json(self):
            return {
                "items": [{"link": f"https://example.invalid/{i}"} for i in range(self.start, self.start + 10)],
                "queries": {"nextPage": [{"startIndex": self.start + 10}]},
            }

    class Session:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response(kwargs["params"]["start"])

    monkeypatch.setattr(module.aiohttp, "ClientSession", Session)
    monkeypatch.setattr(module.EnvService, "get_http_proxy", lambda url: "http://proxy.invalid")
    model = module.Search(None, None)
    selected, fallback = await model.get_links("a&b?", 16)

    assert len(selected) == 16 and len(fallback) == 20
    assert [call[1]["params"]["start"] for call in calls] == [1, 11]
    assert all(call[1]["params"]["q"] == "a&b?" for call in calls)
    assert all(call[1]["proxy"] == "http://proxy.invalid" for call in calls)


def test_search_index_names_are_single_safe_path_components(search, monkeypatch, tmp_path):
    module = importlib.import_module("models.search_model")
    destinations = []
    monkeypatch.setattr(module.EnvService, "save_path", lambda: tmp_path)
    monkeypatch.setattr(module, "persist_index", lambda index, *, persist_dir: destinations.append(Path(persist_dir)))
    module.Search.add_search_index(object(), object(), 42, '/../../../../x\\y?"')
    assert destinations[0].parent == tmp_path / "indexes" / "42_search"
    assert len(destinations[0].relative_to(tmp_path).parts) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("component", [False, True])
async def test_translation_delivers_long_source_and_expanded_result(monkeypatch, component):
    monkeypatch.setenv("ALLOWED_GUILDS", "1")
    module = importlib.import_module("cogs.translation_service_cog")
    source, translated = "a" * 2000, "文" * 8000
    model = SimpleNamespace(send_translate_request=AsyncMock(return_value=(translated, "EN")))
    if component:
        message = SimpleNamespace(content=source, reply=AsyncMock())
        selection = SimpleNamespace(delete=AsyncMock())
        view = module.TranslateView(model, message, selection, requester())
        view.language, view.language_long = "FR", "French"
        interaction = component_interaction()
        interaction.followup = SimpleNamespace(send=AsyncMock())
        await view.button_callback.callback(interaction)
        delivered = message.reply.await_args_list
        selection.delete.assert_awaited_once()
        view.stop()
    else:
        cog = module.TranslationService(None, model)
        ctx = SimpleNamespace(user=requester(), defer=AsyncMock(), respond=AsyncMock())
        await cog.translate_command(ctx, source, "French", None)
        delivered = ctx.respond.await_args_list
    assert len(delivered) == 3
    descriptions = [call.kwargs["embed"].description for call in delivered]
    assert all(len(page) <= 4000 for page in descriptions)
    full = "".join(descriptions)
    assert source in full and translated in full


@pytest.fixture
def code(monkeypatch, tmp_path):
    pytest.importorskip("e2b_code_interpreter")
    pytest.importorskip("langchain_classic")
    pytest.importorskip("langchain_openai")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SHARE_DIR", str(tmp_path))
    module = importlib.import_module("cogs.code_interpreter_service_cog")
    monkeypatch.setattr(module, "USER_INPUT_API_KEYS", False)
    monkeypatch.setattr(module, "GOOGLE_API_KEY", None)
    monkeypatch.setattr(module, "safe_ctx_respond", AsyncMock())
    return module


@pytest.mark.asyncio
async def test_code_initialization_failure_closes_sandbox_and_dead_thread(code, monkeypatch):
    session = SimpleNamespace(
        is_sessioned=lambda: True,
        close=Mock(),
        execute_code_sync=lambda value: value,
        install_python_package=lambda value: value,
        install_system_package=lambda value: value,
        run_command_sync=lambda value: value,
    )
    cog = code.CodeInterpreterService(None, None, None, None, None)
    monkeypatch.setattr(cog, "SessionedCodeExecutor", lambda: session)
    monkeypatch.setattr(code, "create_chat_llm", Mock(side_effect=RuntimeError("PRIVATE invalid key")))
    thread = SimpleNamespace(id=123, send=AsyncMock(), edit=AsyncMock())
    message = SimpleNamespace(create_thread=AsyncMock(return_value=thread))
    ctx = SimpleNamespace(user=requester(), defer=AsyncMock(), send=AsyncMock(return_value=message))

    await cog.code_interpreter_chat_command(ctx, "gpt-6-luna", 0, 1)

    session.close.assert_called_once()
    assert not cog.sessions and not cog.chat_agents and not cog.thread_owners
    thread.edit.assert_awaited_once_with(name="Closed-GPT (Error)", archived=True)
    assert "PRIVATE" not in thread.send.await_args.args[0]


@pytest.mark.asyncio
async def test_expired_code_sandbox_can_still_end_and_archive(code, monkeypatch):
    monkeypatch.setattr(code, "PRE_MODERATE", False)
    cog = code.CodeInterpreterService(SimpleNamespace(fetch_channel=AsyncMock()), None, None, None, None)
    cog.sessions[123] = SimpleNamespace(close=Mock(side_effect=RuntimeError("Expired sandbox")))
    cog.chat_agents[123] = object()
    cog.thread_owners[123] = 42
    thread = SimpleNamespace(edit=AsyncMock())
    cog.bot.fetch_channel.return_value = thread
    message = SimpleNamespace(channel=SimpleNamespace(id=123), content="end", reply=AsyncMock())

    await cog._process_session_message(message)

    assert not cog.sessions and not cog.chat_agents and not cog.thread_owners
    thread.edit.assert_any_await(archived=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("single_attempt,expected", [(False, 3), (True, 1)])
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_optional_agent_clients_honor_request_policy_per_turn(monkeypatch, single_attempt, expected, asynchronous):
    pytest.importorskip("langchain_classic")
    pytest.importorskip("langchain_openai")
    module = importlib.import_module("services.agent_service")
    import httpx
    from openai import InternalServerError
    from services.request_context import single_attempt_requests

    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(503, json={"error": {"message": "Unavailable", "type": "server_error"}})

    if asynchronous:
        client = module._AgentAsyncOpenAI(api_key="offline-key", http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        monkeypatch.setattr(client, "_sleep_for_retry", AsyncMock())
    else:
        client = module._AgentOpenAI(api_key="offline-key", http_client=httpx.Client(transport=httpx.MockTransport(respond)))
        monkeypatch.setattr(client, "_sleep_for_retry", Mock())
    token = single_attempt_requests.set(single_attempt)
    try:
        with pytest.raises(InternalServerError):
            result = client.responses.create(model="gpt-6-luna", input="Test")
            if asynchronous:
                await result
        assert len(calls) == expected
        assert client.max_retries == 2
    finally:
        single_attempt_requests.reset(token)
        if asynchronous:
            await client.close()
        else:
            client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_fails", [False, True])
async def test_original_welcome_feature_generates_and_falls_back_privately(monkeypatch, tmp_path, provider_fails):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALLOWED_GUILDS", "1")
    module = importlib.import_module("cogs.text_service_cog")
    monkeypatch.setattr(module, "member_has_access", lambda member: True)
    monkeypatch.setattr(module.EnvService, "get_welcome_message", lambda: "Configured welcome")
    model = SimpleNamespace(
        welcome_message_enabled=True,
        send_request=AsyncMock(
            return_value={"choices": [{"message": {"content": "Generated welcome"}}]},
            side_effect=RuntimeError("Upstream unavailable") if provider_fails else None,
        ),
    )
    cog = SimpleNamespace(model=model, usage_service=SimpleNamespace(count_tokens=lambda text: 10))
    member = SimpleNamespace(name="New member", send=AsyncMock())

    await module.GPT3ComCon.on_member_join(cog, member)

    model.send_request.assert_awaited_once()
    delivered = member.send.await_args.kwargs["embed"]
    assert delivered.description == ("Configured welcome" if provider_fails else "Generated welcome")
    assert "New member" in delivered.title


@pytest.mark.asyncio
async def test_usage_estimate_survives_service_restart_and_keeps_accumulating(tmp_path):
    from services.usage_service import UsageService

    first = UsageService(tmp_path)
    await first.set_usage(1.25)
    restarted = UsageService(tmp_path)
    await restarted.update_usage_from_response({"input_tokens": 100, "output_tokens": 100}, "gpt-6-luna")
    assert await restarted.get_usage() == pytest.approx(1.25006)


@pytest.mark.asyncio
@pytest.mark.parametrize("integration", ["search", "code"])
@pytest.mark.parametrize("current_key", [None, "rotated-key"])
async def test_optional_sessions_revoke_stale_personal_keys_but_still_allow_end(monkeypatch, tmp_path, integration, current_key):
    pytest.importorskip("langchain_classic")
    pytest.importorskip("langchain_openai")
    pytest.importorskip("llama_index.core")
    pytest.importorskip("e2b_code_interpreter")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SHARE_DIR", str(tmp_path))
    module = importlib.import_module(f"cogs.{'search' if integration == 'search' else 'code_interpreter'}_service_cog")
    from services.agent_service import personal_key_fingerprint

    monkeypatch.setattr(module, "USER_INPUT_API_KEYS", True)
    monkeypatch.setattr(module, "USER_KEY_DB", {} if current_key is None else {42: current_key})
    monkeypatch.setattr(module, "PRE_MODERATE", True)
    monkeypatch.setattr(module, "message_has_access", AsyncMock(return_value=True))
    thread = SimpleNamespace(id=123, edit=AsyncMock(), trigger_typing=AsyncMock())
    bot = SimpleNamespace(fetch_channel=AsyncMock(return_value=thread))
    cls = module.SearchService if integration == "search" else module.CodeInterpreterService
    cog = cls(bot, None, None, None, None)
    agent = SimpleNamespace(invoke=Mock())
    session = SimpleNamespace(close=Mock(), upload_file_async=AsyncMock())
    cog.chat_agents[123] = agent
    cog.thread_owners[123] = 42
    cog.thread_key_fingerprints[123] = personal_key_fingerprint("original-key")
    if integration == "code":
        cog.sessions[123] = session
    author = requester()
    author.bot = False
    author.send = AsyncMock()
    message = SimpleNamespace(
        author=author, channel=thread, guild=SimpleNamespace(name="Test"),
        content="Use my credits", type=discord.MessageType.default,
        attachments=[object()], reply=AsyncMock(), delete=AsyncMock(),
    )

    await cog.on_message(message)

    author.send.assert_awaited_once()
    assert "/setup" in author.send.await_args.args[0]
    agent.invoke.assert_not_called()
    session.upload_file_async.assert_not_awaited()
    assert not cog.thread_awaiting_responses
    message.content = "end"
    await cog.on_message(message)
    assert not cog.chat_agents and not cog.thread_key_fingerprints and not cog.thread_owners
    thread.edit.assert_any_await(archived=True)
    if integration == "code":
        session.close.assert_called_once()
        assert not cog.sessions


@pytest.mark.asyncio
@pytest.mark.parametrize("integration", ["search", "code"])
async def test_optional_chats_register_prepare_and_enroll_scoped_recovery(monkeypatch, tmp_path, integration):
    pytest.importorskip("langchain_classic")
    pytest.importorskip("langchain_openai")
    pytest.importorskip("llama_index.core")
    pytest.importorskip("e2b_code_interpreter")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SHARE_DIR", str(tmp_path))
    module = importlib.import_module(f"cogs.{'search' if integration == 'search' else 'code_interpreter'}_service_cog")
    monkeypatch.setattr(module, "USER_INPUT_API_KEYS", False)
    monkeypatch.setattr(module, "GOOGLE_API_KEY", None)
    monkeypatch.setattr(module, "safe_ctx_respond", AsyncMock())
    monkeypatch.setattr(module, "create_chat_llm", lambda **kwargs: object())
    monkeypatch.setattr(module, "create_chat_agent", lambda *args: SimpleNamespace(memory=SimpleNamespace()))
    if integration == "search":
        monkeypatch.setattr(module, "WOLFRAM_API_KEY", None)
        monkeypatch.setattr(module, "GoogleSearchAPIWrapper", lambda **kwargs: SimpleNamespace(run=lambda query: "result"))
    recovery = SimpleNamespace(
        register_route=Mock(), enabled_for=lambda guild_id: guild_id == 1,
        prepare_context=AsyncMock(return_value=True), watch_thread=AsyncMock(), dispatch=AsyncMock(),
    )
    bot = SimpleNamespace(get_guild=lambda guild_id: None)
    cls = module.SearchService if integration == "search" else module.CodeInterpreterService
    cog = cls(bot, None, None, None, SimpleNamespace(conversation_recovery=recovery))
    if integration == "code":
        session = SimpleNamespace(
            is_sessioned=lambda: True, execute_code_sync=Mock(),
            install_python_package=Mock(), install_system_package=Mock(), run_command_sync=Mock(),
        )
        monkeypatch.setattr(cog, "SessionedCodeExecutor", lambda: session)
    thread = SimpleNamespace(id=123)

    async def create_thread(**kwargs):
        recovery.prepare_context.assert_awaited_once()
        return thread

    ctx = SimpleNamespace(
        user=requester(), guild_id=1, defer=AsyncMock(),
        send=AsyncMock(return_value=SimpleNamespace(create_thread=AsyncMock(side_effect=create_thread))),
    )
    command = cog.search_chat_command if integration == "search" else cog.code_interpreter_chat_command
    await command(ctx, model="gpt-6-luna", temperature=0, top_p=1)

    assert recovery.register_route.call_args.args == (integration,)
    registration = recovery.register_route.call_args.kwargs
    assert registration["handler"] == cog._process_message
    assert registration["channels"]() is cog.chat_agents
    assert registration["owner_id"](123) == 42
    recovery.watch_thread.assert_awaited_once_with(thread, new=True)
    monkeypatch.setattr(module, "message_has_access", AsyncMock(return_value=True))
    message = object()
    await cog.on_message(message)
    recovery.dispatch.assert_awaited_once_with(message, route=integration)


def test_internet_crawler_does_not_need_downloaded_tokenizer_vocabulary(search, monkeypatch):
    from llama_index.core import Document
    import services.usage_service as usage

    monkeypatch.setattr(usage, "_tokenizer_unavailable", True)
    monkeypatch.setattr(search, "read_web_documents", lambda url: [Document(text="Readable source despite unavailable vocabulary.")])
    wrapper = search.CustomTextRequestWrapper()
    assert wrapper.get("https://example.invalid") == "Readable source despite unavailable vocabulary."


@pytest.mark.asyncio
async def test_internet_search_citations_preserve_modern_tlds_and_deduplicate(search, monkeypatch):
    from llama_index.core.base.response.schema import Response
    from llama_index.core.schema import NodeWithScore, TextNode

    source = "https://example.technology/releases?a=one&b=two"
    response = Response(
        response="Current answer",
        source_nodes=[NodeWithScore(node=TextNode(text="Source", metadata={"URL": url})) for url in (source, source, "https://example.photography/news")],
    )
    monkeypatch.setattr(search.EnvService, "get_google_search_api_key", lambda: "offline-google")
    monkeypatch.setattr(search.EnvService, "get_google_search_engine_id", lambda: "offline-engine")
    paginator = SimpleNamespace(respond=AsyncMock())
    factory = Mock(return_value=paginator)
    monkeypatch.setattr(search.pages, "Paginator", factory)
    cog = search.SearchService(None, None, None, None, None)
    cog.model.search = AsyncMock(return_value=(response, "release"))
    ctx = SimpleNamespace(user=requester(), defer=AsyncMock(), interaction=object())

    await cog.search_command(ctx, "What changed?", 2, 2, False, "compact", "gpt-6-luna")

    pages = factory.call_args.kwargs["pages"]
    full = "".join(page.description for page in pages)
    assert full.count(f"<{source}>") == 1
    assert "<https://example.photography/news>" in full
    factory.call_args.kwargs["custom_view"].stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ValueError("PRIVATE key in validation"), RuntimeError("PRIVATE provider body")])
async def test_standalone_internet_search_does_not_echo_provider_or_validation_errors(search, monkeypatch, error):
    monkeypatch.setattr(search.EnvService, "get_google_search_api_key", lambda: "offline-google")
    monkeypatch.setattr(search.EnvService, "get_google_search_engine_id", lambda: "offline-engine")
    cog = search.SearchService(None, None, None, None, None)
    cog.model.search = AsyncMock(side_effect=error)
    ctx = SimpleNamespace(user=requester(), defer=AsyncMock(), respond=AsyncMock())

    await cog.search_command(ctx, "What changed?", 2, 2, False, "compact", "gpt-6-luna")

    assert "PRIVATE" not in ctx.respond.await_args.kwargs["embed"].description
    assert ctx.respond.await_args.kwargs["ephemeral"]


@pytest.mark.asyncio
async def test_cancelled_code_creation_closes_late_sandbox_and_archives_thread(code, monkeypatch):
    started, release, closed = threading.Event(), threading.Event(), threading.Event()
    session = SimpleNamespace(is_sessioned=lambda: True, close=Mock(side_effect=closed.set))

    def create():
        started.set()
        assert release.wait(2), "test must release sandbox creation"
        return session

    cog = code.CodeInterpreterService(None, None, None, None, None)
    monkeypatch.setattr(cog, "SessionedCodeExecutor", create)
    thread = SimpleNamespace(id=123, send=AsyncMock(), edit=AsyncMock())
    ctx = SimpleNamespace(
        user=requester(), defer=AsyncMock(),
        send=AsyncMock(return_value=SimpleNamespace(create_thread=AsyncMock(return_value=thread))),
    )
    command = asyncio.create_task(cog.code_interpreter_chat_command(ctx, "gpt-6-luna", 0, 1))
    try:
        assert await asyncio.to_thread(started.wait, 1)
        command.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(command, 1)
        thread.edit.assert_awaited_once_with(name="Closed-GPT (Error)", archived=True)
        assert not cog.sessions and not cog.chat_agents and not cog.thread_owners
        assert not closed.is_set()
    finally:
        release.set()
        if not command.done():
            command.cancel()
            await asyncio.gather(command, return_exceptions=True)
    assert await asyncio.to_thread(closed.wait, 1)
    await asyncio.wait_for(asyncio.gather(*cog._sandbox_tasks), 1)
    await asyncio.sleep(0)  # Allow task-completion callbacks to discard ownership.
    session.close.assert_called_once()
    assert not cog._sandbox_tasks


@pytest.mark.asyncio
async def test_cancelled_code_enrollment_closes_created_sandbox_and_clears_all_state(code, monkeypatch):
    entered = asyncio.Event()

    async def enroll(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(code, "USER_INPUT_API_KEYS", True)
    monkeypatch.setattr(code, "USER_KEY_DB", {42: "current-key"})
    monkeypatch.setattr(code, "create_chat_llm", lambda **kwargs: object())
    monkeypatch.setattr(code, "create_chat_agent", lambda *args: object())
    recovery = SimpleNamespace(register_route=Mock(), watch_thread=AsyncMock(side_effect=enroll))
    bot = SimpleNamespace(get_guild=lambda guild_id: object())
    cog = code.CodeInterpreterService(bot, None, None, None, SimpleNamespace(conversation_recovery=recovery))
    session = SimpleNamespace(
        is_sessioned=lambda: True, close=Mock(), execute_code_sync=Mock(),
        install_python_package=Mock(), install_system_package=Mock(), run_command_sync=Mock(),
    )
    monkeypatch.setattr(cog, "SessionedCodeExecutor", lambda: session)
    thread = SimpleNamespace(id=123, send=AsyncMock(), edit=AsyncMock())
    ctx = SimpleNamespace(
        user=requester(), guild_id=1, defer=AsyncMock(),
        send=AsyncMock(return_value=SimpleNamespace(create_thread=AsyncMock(return_value=thread))),
    )
    command = asyncio.create_task(cog.code_interpreter_chat_command(ctx, "gpt-6-luna", 0, 1))
    await asyncio.wait_for(entered.wait(), 1)
    assert cog.sessions and cog.chat_agents and cog.thread_owners and cog.thread_key_fingerprints

    command.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(command, 1)

    session.close.assert_called_once()
    assert not cog.sessions and not cog.chat_agents and not cog.thread_owners and not cog.thread_key_fingerprints
    thread.edit.assert_awaited_once_with(name="Closed-GPT (Error)", archived=True)
    assert not cog._sandbox_tasks


@pytest.mark.asyncio
async def test_worker_search_tree_summary_preserves_complete_multi_chunk_response(search, monkeypatch):
    from llama_index.core import Document
    from llama_index.core.callbacks import CallbackManager, TokenCountingHandler
    from llama_index.core.embeddings import MockEmbedding
    from llama_index.core.llms import MockLLM
    from llama_index.core.base.llms.types import LLMMetadata
    from services.indexing_service import IndexContext
    import llama_index.core

    module = importlib.import_module("models.search_model")
    monkeypatch.setattr(llama_index.core, "global_tokenizer", lambda text: text.split())
    summaries = []

    class SyncSummaryLLM(MockLLM):
        @property
        def metadata(self):
            return LLMMetadata(context_window=512, num_output=4)

        def predict(self, prompt, **kwargs):
            summaries.append(kwargs.get("context_str", ""))
            return "Complete answer preserves FENNEL."

        async def apredict(self, *args, **kwargs):
            raise AssertionError("Worker summaries must not create transient async client loops")

    counter = TokenCountingHandler(tokenizer=lambda text: text.split())
    callbacks = CallbackManager([counter])
    context = IndexContext(
        llm=SyncSummaryLLM(max_tokens=4, callback_manager=callbacks),
        embed_model=MockEmbedding(embed_dim=8, callback_manager=callbacks),
        callback_manager=callbacks, token_counter=counter,
    )
    monkeypatch.setattr(module, "create_index_context", lambda **kwargs: context)
    monkeypatch.setattr(module, "create_chat_llm", lambda *args, **kwargs: SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content="FENNEL"))))

    class Session:
        def __init__(self, **kwargs):
            self.headers = {"Content-Type": "text/html"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def get(self, url):
            return self

        def raise_for_status(self):
            pass

    monkeypatch.setattr(module.aiohttp, "ClientSession", Session)
    usage = SimpleNamespace(get_price=AsyncMock(return_value=0.01), get_cost_name=AsyncMock(return_value="gpt-6-luna"), update_usage=AsyncMock())
    model = module.Search(None, usage)
    monkeypatch.setattr(model, "get_links", AsyncMock(return_value=(["https://example.invalid"], ["https://example.invalid"])))
    monkeypatch.setattr(model, "index_webpage", lambda url: [Document(text="FENNEL evidence is retained. " * 1000, metadata={"URL": url})])

    response, _ = await model.search(None, "Which evidence is retained?", "personal-key", 1, 4, False, "tree_summarize", "gpt-6-luna")

    assert len(summaries) > 2
    assert response.response == "Complete answer preserves FENNEL."
    assert all("FENNEL" in text for text in summaries)
