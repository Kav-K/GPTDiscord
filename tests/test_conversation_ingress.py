"""Gateway and REST deliveries must share one paid conversation ingress."""

import asyncio
from datetime import datetime
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

GUILD_ID = 1061370086331523176
THREAD_ID = 1168110042826940447
BOT_ID = 1168110042826940446
MAINTAINER_ROLE = 1062146522365571123
OWNER_ID = 42
PARTICIPANT_ID = 43


def message(
    message_id=1001, *, channel_id=THREAD_ID, content="A new question", edited_at=None
):
    if isinstance(edited_at, str):
        edited_at = datetime.fromisoformat(edited_at)
    guild = SimpleNamespace(id=GUILD_ID, get_thread=lambda _: None)
    channel = discord.Thread(
        guild=guild,
        state=SimpleNamespace(),
        data={
            "id": str(channel_id),
            "guild_id": str(GUILD_ID),
            "parent_id": str(THREAD_ID - 1),
            "name": "Tracked conversation",
            "type": 11,
            "owner_id": str(BOT_ID),
            "thread_metadata": {
                "archived": False,
                "auto_archive_duration": 60,
                "archive_timestamp": "2026-10-17T00:00:00+00:00",
                "locked": False,
            },
        },
    )
    author = SimpleNamespace(id=PARTICIPANT_ID, bot=False, display_name="Maintainer")
    return SimpleNamespace(
        id=message_id,
        guild=guild,
        channel=channel,
        author=author,
        content=content,
        attachments=[],
        type=discord.MessageType.default,
        edited_at=edited_at,
        webhook_id=None,
        _raw_data={
            "guild_id": str(GUILD_ID),
            "member": {"roles": [str(MAINTAINER_ROLE)]},
        },
        reply=AsyncMock(),
    )


@pytest.fixture
def ingress(monkeypatch, tmp_path):
    monkeypatch.setenv("ALLOWED_GUILDS", str(GUILD_ID))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SHARE_DIR", str(tmp_path))
    monkeypatch.setenv("BOT_ACCESS_ROLE_IDS", str(MAINTAINER_ROLE))
    monkeypatch.setenv("CONVERSATION_RECOVERY_GUILD_IDS", str(GUILD_ID))
    module = importlib.import_module("services.conversation_recovery_service")
    http = SimpleNamespace(
        get_member=AsyncMock(
            return_value={
                "roles": [str(MAINTAINER_ROLE)],
                "user": {
                    "id": str(PARTICIPANT_ID),
                    "username": "Maintainer",
                    "discriminator": "0",
                },
            }
        )
    )
    bot = SimpleNamespace(
        user=SimpleNamespace(id=BOT_ID), http=http, get_guild=lambda _: None
    )
    cog = SimpleNamespace(
        bot=bot,
        conversation_threads={THREAD_ID: object(), THREAD_ID + 1: object()},
        conversation_thread_owners={OWNER_ID: [THREAD_ID, THREAD_ID + 1]},
        awaiting_responses=[],
        awaiting_thread_responses=[],
        _process_message=AsyncMock(return_value=True),
        _process_message_edit=AsyncMock(return_value=True),
    )
    service = module.ConversationRecoveryService(cog, tmp_path, guild_ids={GUILD_ID})
    return module, service, cog


@pytest.mark.asyncio
async def test_concurrent_gateway_and_rest_delivery_claims_one_generation(ingress):
    _, service, cog = ingress
    started, finish = asyncio.Event(), asyncio.Event()

    async def generate(_, **kwargs):
        started.set()
        await finish.wait()
        return True

    cog._process_message.side_effect = generate
    first = asyncio.create_task(service.dispatch(message(), source="gateway"))
    await asyncio.wait_for(started.wait(), 1)
    duplicate = asyncio.create_task(service.dispatch(message(), source="rest"))
    await asyncio.sleep(0)
    finish.set()
    await asyncio.gather(first, duplicate)
    cog._process_message.assert_awaited_once()
    await service.close()


@pytest.mark.asyncio
async def test_different_turns_and_edits_in_one_thread_are_serialized(ingress):
    _, service, cog = ingress
    started, finish = asyncio.Event(), asyncio.Event()
    order = []

    async def create(value, **kwargs):
        order.append(("create", value.id))
        if value.id == 1001:
            started.set()
            await finish.wait()
        return True

    async def edit(value):
        order.append(("edit", value.id))
        return True

    cog._process_message.side_effect = create
    cog._process_message_edit.side_effect = edit
    first = asyncio.create_task(service.dispatch(message(1001)))
    await asyncio.wait_for(started.wait(), 1)
    second = asyncio.create_task(service.dispatch(message(1002)))
    changed = asyncio.create_task(
        service.dispatch(message(1001, content="An edited question"), edited=True)
    )
    await asyncio.sleep(0)
    assert order == [("create", 1001)]
    finish.set()
    await asyncio.gather(first, second, changed)
    assert order == [("create", 1001), ("create", 1002), ("edit", 1001)]
    await service.close()


@pytest.mark.asyncio
async def test_independent_conversation_threads_do_not_block_each_other(ingress):
    _, service, cog = ingress
    first_started, second_started, finish = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )

    async def generate(value, **kwargs):
        if value.channel.id == THREAD_ID:
            first_started.set()
            await finish.wait()
        else:
            second_started.set()
        return True

    cog._process_message.side_effect = generate
    first = asyncio.create_task(service.dispatch(message(1001)))
    await asyncio.wait_for(first_started.wait(), 1)
    second = asyncio.create_task(
        service.dispatch(message(1002, channel_id=THREAD_ID + 1))
    )
    try:
        await asyncio.wait_for(second_started.wait(), 1)
    finally:
        finish.set()
        await asyncio.gather(first, second)
        await service.close()
    assert cog._process_message.await_count == 2


@pytest.mark.asyncio
async def test_metadata_only_edit_cannot_regenerate_the_same_prompt(ingress):
    _, service, cog = ingress
    await service.dispatch(message())
    await service.dispatch(message(edited_at="2026-10-17T00:00:01+00:00"), edited=True)
    cog._process_message.assert_awaited_once()
    cog._process_message_edit.assert_not_awaited()
    await service.close()


@pytest.mark.asyncio
async def test_cancelled_generation_keeps_claim_and_cleans_only_its_own_waiters(
    ingress,
):
    _, service, cog = ingress
    started = asyncio.Event()
    cog.awaiting_responses.append(9999)
    cog.awaiting_thread_responses.append(9998)

    async def generate(value, **kwargs):
        cog.awaiting_responses.append(value.author.id)
        cog.awaiting_thread_responses.append(value.channel.id)
        started.set()
        await asyncio.Future()

    cog._process_message.side_effect = generate
    pending = asyncio.create_task(service.dispatch(message()))
    await asyncio.wait_for(started.wait(), 1)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert cog.awaiting_responses == [9999]
    assert cog.awaiting_thread_responses == [9998]
    cog._process_message.side_effect = None
    await service.dispatch(message(), source="rest")
    cog._process_message.assert_awaited_once()
    await service.close()


@pytest.mark.asyncio
async def test_shared_maintainer_conversations_do_not_become_owner_only(ingress):
    _, service, cog = ingress
    # Ownership establishes that the bot tracks the thread. Existing authorized
    # participants may continue speaking without becoming its registered owner.
    assert PARTICIPANT_ID not in cog.conversation_thread_owners
    await service.dispatch(message())
    cog._process_message.assert_awaited_once()
    await service.close()


@pytest.mark.asyncio
async def test_actual_gateway_listeners_use_the_same_claims_as_rest(ingress):
    _, service, cog = ingress
    module = importlib.import_module("cogs.text_service_cog")
    cog.conversation_recovery = service
    initial = message()
    await module.GPT3ComCon.on_message(cog, initial)
    await service.dispatch(message(), source="rest")
    await module.GPT3ComCon.on_message_edit(cog, initial, message())
    changed = message(content="The revised question")
    await module.GPT3ComCon.on_message_edit(cog, initial, changed)
    await module.GPT3ComCon.on_message_edit(cog, initial, changed)
    cog._process_message.assert_awaited_once()
    cog._process_message_edit.assert_awaited_once_with(changed)
    await service.close()


@pytest.mark.asyncio
async def test_actual_gateway_listener_does_not_trust_cached_maintainer_roles(ingress):
    _, service, cog = ingress
    module = importlib.import_module("cogs.text_service_cog")
    cog.conversation_recovery = service
    denied = message()
    denied.author._roles = [MAINTAINER_ROLE]
    denied._raw_data["member"]["roles"] = []
    await module.GPT3ComCon.on_message(cog, denied)
    await module.GPT3ComCon.on_message_edit(cog, denied, denied)
    cog._process_message.assert_not_awaited()
    cog._process_message_edit.assert_not_awaited()
    await service.close()


@pytest.mark.asyncio
async def test_rest_retry_policy_is_local_to_the_dispatch_task(ingress):
    _, service, cog = ingress
    from services.request_context import single_attempt_requests

    observed = []

    async def generate(value, **kwargs):
        observed.append(single_attempt_requests.get())
        return True

    cog._process_message.side_effect = generate
    await service.dispatch(message(1001), source="rest")
    assert single_attempt_requests.get() is False
    await service.dispatch(message(1002), source="gateway")
    assert observed == [True, False]
    assert single_attempt_requests.get() is False
    await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("replace", [False, True], ids=["ended", "replaced"])
async def test_completed_provider_cannot_reply_into_ended_or_replaced_conversation(
    ingress,
    replace,
):
    _, service, _ = ingress
    module = importlib.import_module("services.text_service")
    from models.openai_model import Override
    from models.user_model import EmbeddedConversationItem, Thread

    original, replacement = Thread(THREAD_ID), Thread(THREAD_ID)
    original.history = [EmbeddedConversationItem("Original conversation", 0)]
    replacement.history = [EmbeddedConversationItem("Replacement conversation", 0)]
    ctx = message()
    ctx.channel = SimpleNamespace(id=THREAD_ID, send=AsyncMock())
    cog = SimpleNamespace(
        conversation_threads={THREAD_ID: original},
        pinecone_service=None,
        instructions={},
        usage_service=SimpleNamespace(count_tokens=Mock(return_value=1)),
        cleanse_response=lambda content: content,
        mention_to_username=AsyncMock(side_effect=lambda _, content: content),
        remove_awaiting=Mock(),
    )

    async def generate(*args, **kwargs):
        if replace:
            cog.conversation_threads[THREAD_ID] = replacement
        else:
            cog.conversation_threads.pop(THREAD_ID)
        return {"choices": [{"message": {"content": "An obsolete answer"}}]}

    cog.model = SimpleNamespace(
        summarize_threshold=100000,
        send_chatgpt_chat_request=AsyncMock(side_effect=generate),
        send_request=AsyncMock(),
    )
    await module.TextService.encapsulated_send(
        cog,
        THREAD_ID,
        "Original question",
        ctx,
        overrides=Override(),
        model="gpt-6-luna",
    )
    cog.model.send_chatgpt_chat_request.assert_awaited_once()
    cog.model.send_request.assert_not_awaited()
    ctx.reply.assert_not_awaited()
    ctx.channel.send.assert_not_awaited()
    assert [item.text for item in original.history] == ["Original conversation"]
    assert [item.text for item in replacement.history] == ["Replacement conversation"]
    await service.close()


@pytest.mark.asyncio
async def test_owner_cannot_end_shared_thread_while_another_maintainer_is_waiting(
    ingress,
):
    _, service, cog = ingress
    module = importlib.import_module("cogs.text_service_cog")
    ctx = message()
    ctx.author.id = OWNER_ID
    cog.awaiting_responses.append(PARTICIPANT_ID)
    cog.awaiting_thread_responses.append(THREAD_ID)
    await module.GPT3ComCon.end_conversation(cog, ctx)
    assert THREAD_ID in cog.conversation_threads
    assert cog.conversation_thread_owners[OWNER_ID] == [THREAD_ID, THREAD_ID + 1]
    ctx.reply.assert_awaited_once()
    assert "wait" in ctx.reply.await_args.kwargs["embed"].title.lower()
    await service.close()


@pytest.mark.asyncio
async def test_rest_retry_policy_resets_when_the_provider_raises(ingress):
    _, service, cog = ingress
    from services.request_context import single_attempt_requests

    async def fail(value, **kwargs):
        assert single_attempt_requests.get() is True
        raise RuntimeError("An ambiguous provider failure")

    cog._process_message.side_effect = fail
    with pytest.raises(RuntimeError, match="ambiguous"):
        await service.dispatch(message(), source="rest")
    assert single_attempt_requests.get() is False
    await service.dispatch(message(), source="gateway")
    cog._process_message.assert_awaited_once()
    await service.close()


@pytest.mark.asyncio
async def test_rest_provider_failure_is_one_attempt_without_changing_gateway_retries(
    ingress,
    monkeypatch,
):
    _, service, cog = ingress
    module = importlib.import_module("models.openai_model")
    import backoff._async

    attempts = []

    class Response:
        status = 503

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def json(self):
            return {"error": {"message": "Temporarily unavailable"}}

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def post(self, url, **kwargs):
            attempts.append(url)
            return Response()

    monkeypatch.setattr(module.aiohttp, "ClientSession", lambda **kwargs: Session())
    monkeypatch.setattr(module.EnvService, "get_http_proxy", lambda _: None)
    monkeypatch.setattr(backoff._async.asyncio, "sleep", AsyncMock())
    model = SimpleNamespace(
        openai_base_url="https://offline-provider.invalid/v1",
        _request_headers=lambda _: {},
    )

    async def generate(value, **kwargs):
        return await module.Model._post_json(
            model, "chat/completions", {"model": "gpt-6-luna"}
        )

    cog._process_message.side_effect = generate
    with pytest.raises(module.OpenAIAPIError):
        await service.dispatch(message(1001), source="rest")
    assert len(attempts) == 1
    with pytest.raises(module.OpenAIAPIError):
        await service.dispatch(message(1002), source="gateway")
    assert len(attempts) == 5
    await service.close()


@pytest.mark.asyncio
async def test_a_dated_content_revert_is_one_new_edit_not_a_duplicate_create(ingress):
    _, service, cog = ingress
    await service.dispatch(message(content="Version A"))
    version_b = message(content="Version B", edited_at="2026-10-17T00:00:01+00:00")
    version_a_again = message(
        content="Version A", edited_at="2026-10-17T00:00:02+00:00"
    )
    await service.dispatch(version_b, edited=True)
    await service.dispatch(version_a_again, edited=True)
    await service.dispatch(version_a_again, edited=True)
    await service.dispatch(message(content="Version A"), source="rest")
    cog._process_message.assert_awaited_once()
    assert [
        call.args[0].content for call in cog._process_message_edit.await_args_list
    ] == ["Version B", "Version A"]
    await service.close()


@pytest.mark.asyncio
async def test_rest_retry_policy_resets_when_dispatch_is_cancelled(ingress):
    _, service, cog = ingress
    from services.request_context import single_attempt_requests

    async def cancel(value, **kwargs):
        assert single_attempt_requests.get() is True
        raise asyncio.CancelledError()

    cog._process_message.side_effect = cancel
    with pytest.raises(asyncio.CancelledError):
        await service.dispatch(message(), source="rest")
    assert single_attempt_requests.get() is False
    cog._process_message.side_effect = None
    await service.dispatch(message(), source="gateway")
    cog._process_message.assert_awaited_once()
    await service.close()
