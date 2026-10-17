"""Offline REST recovery contracts using real Discord guild/thread/message models.

Discord HTTP and the application/provider callback are the only boundaries
simulated here. No gateway connection, credentials, or paid requests are used.
"""

import asyncio
from collections import defaultdict
from copy import deepcopy
import pickle
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from tests.test_conversation_flow import (
    BOT,
    BOT_ROLE,
    CLONE,
    GUILD,
    MAINTAINER,
    MEMBER,
    PARENT,
    STAMP,
    conversation,
    member_payload,
    message_payload,
    user_payload,
)

THREAD = 1500000000000000100
FIRST = THREAD + 10


def thread_payload(identifier=THREAD, *, owner=BOT, guild=GUILD, archived=False):
    return {
        "id": str(identifier),
        "guild_id": str(guild),
        "parent_id": str(PARENT),
        "name": "An active GPT conversation",
        "type": 11,
        "owner_id": str(owner),
        "message_count": 0,
        "member_count": 2,
        "thread_metadata": {
            "archived": archived,
            "auto_archive_duration": 60,
            "archive_timestamp": STAMP,
            "locked": False,
        },
    }


def guild_payload():
    def role(identifier, name, *, admin=False):
        return {
            "id": str(identifier),
            "name": name,
            "permissions": str(discord.Permissions(administrator=admin).value),
            "position": 1,
            "color": 0,
            "colors": {
                "primary_color": 0,
                "secondary_color": None,
                "tertiary_color": None,
            },
            "hoist": False,
            "managed": False,
            "mentionable": False,
        }

    return {
        "id": str(GUILD),
        "name": "Recovery guild without a gateway subscription",
        "owner_id": str(MEMBER),
        "roles": [
            role(GUILD, "@everyone"),
            role(MAINTAINER, "Maintainer"),
            role(CLONE, "Maintainer", admin=True),
            role(BOT_ROLE, "GPTDiscord"),
        ],
        "members": [member_payload(), member_payload(BOT, bot=True)],
        "channels": [
            {
                "id": str(PARENT),
                "guild_id": str(GUILD),
                "name": "gpt",
                "type": 0,
                "position": 0,
                "permission_overwrites": [],
                "nsfw": False,
            }
        ],
        "threads": [],
        "emojis": [],
        "features": [],
        "member_count": 2,
        "verification_level": 0,
        "default_message_notifications": 0,
        "explicit_content_filter": 0,
        "mfa_level": 0,
        "afk_timeout": 300,
    }


@pytest.fixture
async def recovery(monkeypatch, tmp_path):
    from services.conversation_recovery_service import ConversationRecoveryService

    monkeypatch.setenv("BOT_ACCESS_ROLE_IDS", str(MAINTAINER))
    monkeypatch.setenv("ALLOWED_GUILDS", str(GUILD))
    monkeypatch.setenv("CONVERSATION_RECOVERY_GUILD_IDS", str(GUILD))
    bot = discord.Bot(intents=discord.Intents.all(), auto_sync_commands=False)
    state = bot._connection
    state.user = discord.ClientUser(state=state, data=user_payload(BOT, bot=True))
    guild = discord.Guild(state=state, data=guild_payload())
    thread = discord.Thread(guild=guild, state=state, data=thread_payload())
    guild._threads[THREAD] = thread
    processed = []

    async def process(message, **kwargs):
        processed.append(("create", message.id, message.content))

    async def process_edit(message):
        processed.append(("edit", message.id, message.content))

    cog = SimpleNamespace(
        bot=bot,
        conversation_threads={THREAD: SimpleNamespace(history=[])},
        conversation_thread_owners=defaultdict(list, {MEMBER: [THREAD]}),
        awaiting_responses=[],
        awaiting_thread_responses=[],
        _process_message=AsyncMock(side_effect=process),
        _process_message_edit=AsyncMock(side_effect=process_edit),
    )
    member_http = AsyncMock(return_value=member_payload())
    monkeypatch.setattr(state.http, "get_member", member_http)
    monkeypatch.setattr(
        state.http, "get_channel", AsyncMock(return_value=thread_payload())
    )
    monkeypatch.setattr(
        state.http, "get_guild", AsyncMock(return_value=guild_payload())
    )
    monkeypatch.setattr(
        state.http, "get_roles", AsyncMock(return_value=guild_payload()["roles"])
    )
    service = ConversationRecoveryService(
        cog, tmp_path, guild_ids=[GUILD], interval=0.01
    )

    def message(identifier=FIRST, content="A fresh Maintainer question", **kwargs):
        payload = message_payload(identifier, THREAD, content, **kwargs)
        return discord.Message(state=state, channel=thread, data=deepcopy(payload))

    fixture = SimpleNamespace(
        service=service,
        cog=cog,
        bot=bot,
        state=state,
        guild=guild,
        thread=thread,
        member_http=member_http,
        processed=processed,
        message=message,
        data_path=tmp_path,
    )
    try:
        yield fixture
    finally:
        await service.close()
        await bot.close()


async def test_create_and_edit_claims_survive_restart_and_gateway_rest_overlap(
    recovery,
):
    from services.conversation_recovery_service import ConversationRecoveryService

    flow = recovery
    await flow.service.dispatch(flow.message(), source="rest")
    await flow.service.dispatch(flow.message(), source="gateway")
    await flow.service.dispatch(flow.message(), source="rest")
    await flow.service.dispatch(flow.message(content="A revised question"), edited=True)
    await flow.service.dispatch(flow.message(content="A revised question"), edited=True)
    assert flow.processed == [
        ("create", FIRST, "A fresh Maintainer question"),
        ("edit", FIRST, "A revised question"),
    ]
    await flow.service.close()
    restarted = ConversationRecoveryService(flow.cog, flow.data_path, guild_ids=[GUILD])
    try:
        await restarted.dispatch(flow.message(), source="rest")
        await restarted.dispatch(
            flow.message(content="A revised question"), edited=True
        )
        assert len(flow.processed) == 2
        await restarted.dispatch(flow.message(content="A second revision"), edited=True)
        assert flow.processed[-1] == ("edit", FIRST, "A second revision")
    finally:
        await restarted.close()


async def test_changed_rest_snapshot_is_not_a_second_create_but_gateway_edit_can_run(
    recovery,
):
    flow = recovery
    await flow.service.dispatch(flow.message(), source="rest")
    await flow.service.dispatch(
        flow.message(content="A changed REST snapshot"), source="rest"
    )
    assert len(flow.processed) == 1
    await flow.service.dispatch(
        flow.message(content="A changed REST snapshot"), edited=True
    )
    assert flow.processed[-1] == ("edit", FIRST, "A changed REST snapshot")


async def test_attachment_revision_is_claimed_once_even_when_text_is_unchanged(
    recovery,
):
    flow = recovery
    await flow.service.dispatch(flow.message(), source="gateway")

    def with_attachment(identifier):
        message = flow.message()
        message.attachments = [
            discord.Attachment(
                state=flow.state,
                data={
                    "id": str(identifier),
                    "filename": "question.txt",
                    "size": 12,
                    "url": "https://cdn.invalid/question.txt",
                    "proxy_url": "https://proxy.invalid/question.txt",
                },
            )
        ]
        return message

    await flow.service.dispatch(with_attachment(FIRST + 20), edited=True)
    await flow.service.dispatch(with_attachment(FIRST + 20), edited=True)
    await flow.service.dispatch(with_attachment(FIRST + 21), edited=True)
    assert [event for event, _, _ in flow.processed] == ["create", "edit", "edit"]


async def test_failed_provider_attempt_is_not_repeated_after_restart(recovery):
    from services.conversation_recovery_service import ConversationRecoveryService

    flow = recovery
    flow.cog._process_message.side_effect = TimeoutError("offline provider failure")
    with pytest.raises(TimeoutError):
        await flow.service.dispatch(flow.message(), source="rest")
    await flow.service.close()
    restarted = ConversationRecoveryService(flow.cog, flow.data_path, guild_ids=[GUILD])
    try:
        await restarted.dispatch(flow.message(), source="rest")
        assert flow.cog._process_message.await_count == 1
        assert not flow.cog.awaiting_responses
        assert not flow.cog.awaiting_thread_responses
    finally:
        await restarted.close()


@pytest.mark.parametrize("roles", [[], [CLONE]], ids=["role-removed", "clone-admin"])
async def test_rest_roles_are_fresh_and_denial_does_not_claim_or_change_history(
    recovery, roles
):
    flow = recovery
    flow.member_http.return_value = member_payload(roles=roles)
    message = flow.message()  # Incoming snapshot still has the Maintainer role.
    assert message.author._roles.has(MAINTAINER)
    original_history = deepcopy(flow.cog.conversation_threads[THREAD].history)
    await flow.service.dispatch(message, source="rest")
    assert flow.processed == []
    assert flow.cog.conversation_threads[THREAD].history == original_history
    flow.member_http.assert_awaited_once_with(GUILD, MEMBER)

    # A denied event must not consume its durable claim: a later authorized
    # delivery of that exact message can still be handled once.
    flow.member_http.return_value = member_payload()
    await flow.service.dispatch(flow.message(), source="rest")
    assert len(flow.processed) == 1


@pytest.mark.parametrize("kind", ["bot", "webhook", "system"])
async def test_bot_webhook_and_system_messages_never_lookup_members_or_run_provider(
    recovery, kind
):
    flow = recovery
    message = flow.message(bot=kind == "bot", kind=7 if kind == "system" else 0)
    if kind == "webhook":
        message.webhook_id = FIRST + 100
    await flow.service.dispatch(message, source="rest")
    assert flow.processed == []
    flow.member_http.assert_not_awaited()


@pytest.mark.parametrize("kind", ["bot", "webhook", "system"])
async def test_ignored_raw_payload_with_unparseable_reference_does_not_block_next_turn(
    recovery, monkeypatch, kind
):
    flow = recovery
    monkeypatch.setattr(flow.service, "start", lambda: None)
    await flow.service.watch_thread(flow.thread, new=True)
    ignored = message_payload(
        FIRST,
        THREAD,
        "An ignored fixture with an incomplete embedded reference",
        bot=kind == "bot",
        kind=7 if kind == "system" else 0,
        reference=THREAD,
    )
    if kind == "webhook":
        ignored["webhook_id"] = str(FIRST + 100)
    ignored["referenced_message"] = message_payload(FIRST - 1, THREAD, "")
    ignored["referenced_message"]["author"] = None
    with pytest.raises((AttributeError, TypeError)):
        discord.Message(state=flow.state, channel=flow.thread, data=deepcopy(ignored))
    allowed = message_payload(
        FIRST + 1, THREAD, "The next valid question must be answered"
    )
    logs = AsyncMock(return_value=[allowed, ignored])
    monkeypatch.setattr(flow.state.http, "logs_from", logs)
    await flow.service.poll_once()
    assert flow.processed == [("create", FIRST + 1, allowed["content"])]
    flow.member_http.assert_awaited_once_with(GUILD, MEMBER)
    await flow.service.poll_once()
    assert logs.await_args.kwargs["after"] == FIRST + 1
    assert len(flow.processed) == 1


async def test_rest_recovery_is_create_only_and_does_not_revise_answers(recovery):
    flow = recovery
    await flow.service.dispatch(flow.message(), source="rest")
    await flow.service.dispatch(
        flow.message(content="A REST edit is outside the recovery scope"),
        edited=True,
        source="rest",
    )
    assert len(flow.processed) == 1
    flow.cog._process_message_edit.assert_not_awaited()


async def test_new_thread_starts_at_creation_and_handles_all_new_messages_in_order(
    recovery, monkeypatch
):
    flow = recovery
    monkeypatch.setattr(flow.service, "start", lambda: None)
    logs = AsyncMock(
        return_value=[
            message_payload(FIRST + 1, THREAD, "Second question"),
            message_payload(THREAD, THREAD, "The existing starter", bot=True),
            message_payload(FIRST, THREAD, "First question"),
        ]
    )
    monkeypatch.setattr(flow.state.http, "logs_from", logs)
    await flow.service.watch_thread(flow.thread, new=True)
    await flow.service.poll_once()
    assert flow.processed == [
        ("create", FIRST, "First question"),
        ("create", FIRST + 1, "Second question"),
    ]
    assert logs.await_args.kwargs["after"] == THREAD
    await flow.service.poll_once()
    assert len(flow.processed) == 2
    assert logs.await_args.kwargs["after"] == FIRST + 1


async def test_existing_thread_baselines_without_replay_and_restart_keeps_cursor(
    recovery, monkeypatch
):
    from services.conversation_recovery_service import ConversationRecoveryService

    flow = recovery
    flow.member_http.side_effect = lambda guild, member: member_payload(
        member, bot=member == BOT
    )
    logs = AsyncMock(return_value=[message_payload(FIRST, THREAD, "Old backlog")])
    monkeypatch.setattr(flow.state.http, "logs_from", logs)
    await flow.service.poll_once()
    assert flow.processed == []
    logs.assert_awaited_once_with(THREAD, 1)
    await flow.service.close()
    restarted = ConversationRecoveryService(flow.cog, flow.data_path, guild_ids=[GUILD])
    logs.return_value = [message_payload(FIRST + 1, THREAD, "A new question")]
    try:
        await restarted.poll_once()
        assert logs.await_args.kwargs["after"] == FIRST
        assert flow.processed == [("create", FIRST + 1, "A new question")]
        await restarted.poll_once()
        assert len(flow.processed) == 1
    finally:
        await restarted.close()


async def test_archived_thread_baselines_then_first_unarchiving_post_is_delivered_once(
    recovery, monkeypatch
):
    flow = recovery
    flow.service._startup_marker = FIRST + 100
    flow.member_http.side_effect = lambda guild, member: member_payload(
        member, bot=member == BOT
    )
    flow.state.http.get_channel.return_value = thread_payload(archived=True)
    old = message_payload(FIRST, THREAD, "An old question before the bot restarted")
    new = message_payload(FIRST + 1, THREAD, "The first post reopens this thread")
    logs = AsyncMock(return_value=[old])
    monkeypatch.setattr(flow.state.http, "logs_from", logs)

    await flow.service.poll_once()
    assert flow.processed == []
    logs.assert_awaited_once_with(THREAD, 1)

    # Discord automatically unarchives a thread when a member sends a message.
    flow.state.http.get_channel.return_value = thread_payload(archived=False)
    logs.return_value = [new, old]
    await flow.service.poll_once()
    assert logs.await_args.kwargs["after"] == FIRST
    assert flow.processed == [("create", FIRST + 1, new["content"])]
    await flow.service.poll_once()
    assert logs.await_args.kwargs["after"] == FIRST + 1
    assert len(flow.processed) == 1


async def test_message_arriving_after_startup_before_first_poll_is_not_baselined_away(
    recovery, monkeypatch
):
    flow = recovery
    flow.service._startup_marker = FIRST
    flow.member_http.side_effect = lambda guild, member: member_payload(
        member, bot=member == BOT
    )
    old = message_payload(FIRST - 1, THREAD, "Old backlog must stay skipped")
    new = message_payload(
        FIRST + 1, THREAD, "A question during startup's first seconds"
    )

    async def history(channel, limit, **kwargs):
        return [new] if limit == 1 else [new, old]

    logs = AsyncMock(side_effect=history)
    monkeypatch.setattr(flow.state.http, "logs_from", logs)

    await flow.service.poll_once()
    assert logs.await_args.kwargs["after"] == FIRST
    assert flow.processed == [("create", FIRST + 1, new["content"])]
    await flow.service.poll_once()
    assert logs.await_args.kwargs["after"] == FIRST + 1
    assert len(flow.processed) == 1


async def test_transient_role_lookup_retains_cursor_and_batch_until_recovery(
    recovery, monkeypatch
):
    import services.conversation_recovery_service as recovery_module

    flow = recovery
    monkeypatch.setattr(flow.service, "start", lambda: None)
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(
        recovery_module, "time", SimpleNamespace(monotonic=lambda: clock.now)
    )
    await flow.service.watch_thread(flow.thread, new=True)
    payloads = [
        message_payload(FIRST + 1, THREAD, "Second question must wait for first"),
        message_payload(FIRST, THREAD, "First question needs a fresh role lookup"),
    ]
    logs = AsyncMock(return_value=payloads)
    monkeypatch.setattr(flow.state.http, "logs_from", logs)
    flow.member_http.side_effect = TimeoutError("offline transient membership failure")
    await flow.service.poll_once()
    assert flow.processed == []
    assert (
        flow.member_http.await_count == 1
    ), "A retryable first message must stop its batch"

    flow.member_http.side_effect = None
    flow.member_http.return_value = member_payload()
    clock.now += 120
    await flow.service.poll_once()
    assert logs.await_args.kwargs["after"] == THREAD
    assert flow.processed == [
        ("create", FIRST, payloads[1]["content"]),
        ("create", FIRST + 1, payloads[0]["content"]),
    ]
    await flow.service.poll_once()
    assert len(flow.processed) == 2


async def test_post_claim_provider_failure_is_acknowledged_without_second_paid_attempt(
    recovery, monkeypatch
):
    import services.conversation_recovery_service as recovery_module

    flow = recovery
    monkeypatch.setattr(flow.service, "start", lambda: None)
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(
        recovery_module, "time", SimpleNamespace(monotonic=lambda: clock.now)
    )
    await flow.service.watch_thread(flow.thread, new=True)
    payload = message_payload(
        FIRST, THREAD, "A request whose provider outcome is unknown"
    )
    logs = AsyncMock(return_value=[payload])
    monkeypatch.setattr(flow.state.http, "logs_from", logs)
    flow.cog._process_message.side_effect = TimeoutError("offline provider timeout")
    await flow.service.poll_once()
    assert flow.cog._process_message.await_count == 1

    clock.now += 120
    await flow.service.poll_once()
    assert flow.cog._process_message.await_count == 1
    await flow.service.poll_once()
    assert logs.await_args.kwargs["after"] == FIRST
    assert flow.cog._process_message.await_count == 1


@pytest.mark.parametrize("denial", ["removed-role", "left-guild"])
async def test_definitive_role_denial_advances_past_denied_message_to_authorized_turn(
    recovery, monkeypatch, denial
):
    flow = recovery
    monkeypatch.setattr(flow.service, "start", lambda: None)
    await flow.service.watch_thread(flow.thread, new=True)
    denied = message_payload(FIRST, THREAD, "A member whose required role was removed")
    allowed = message_payload(
        FIRST + 1, THREAD, "Another Maintainer can still get a reply"
    )
    allowed["author"] = user_payload(MEMBER + 1)
    allowed["member"] = member_payload(MEMBER + 1)
    logs = AsyncMock(return_value=[allowed, denied])
    monkeypatch.setattr(flow.state.http, "logs_from", logs)

    async def lookup(guild, member):
        if member == MEMBER and denial == "left-guild":
            raise discord.NotFound(
                SimpleNamespace(status=404, reason="Not Found"),
                {"code": 10007, "message": "Unknown Member"},
            )
        return member_payload(member, roles=[] if member == MEMBER else [MAINTAINER])

    flow.member_http.side_effect = lookup
    await flow.service.poll_once()
    assert flow.processed == [("create", FIRST + 1, allowed["content"])]
    await flow.service.poll_once()
    assert logs.await_args.kwargs["after"] == FIRST + 1
    assert len(flow.processed) == 1
    assert flow.member_http.await_count == 2


async def test_gateway_recovery_during_fresh_role_lookup_does_not_consume_message(
    recovery, monkeypatch
):
    flow = recovery
    monkeypatch.setattr(flow.service, "start", lambda: None)
    await flow.service.watch_thread(flow.thread, new=True)
    payload = message_payload(FIRST, THREAD, "A message at the transport transition")
    logs = AsyncMock(return_value=[payload])
    monkeypatch.setattr(flow.state.http, "logs_from", logs)

    async def gateway_becomes_available(guild, member):
        flow.state._guilds[GUILD] = flow.guild
        return member_payload()

    flow.member_http.side_effect = gateway_becomes_available
    await flow.service.poll_once()
    assert flow.processed == []
    flow.state._guilds.clear()
    flow.member_http.side_effect = None
    flow.member_http.return_value = member_payload()
    await flow.service.poll_once()
    assert logs.await_args.kwargs["after"] == THREAD
    assert flow.processed == [("create", FIRST, payload["content"])]
    await flow.service.poll_once()
    assert len(flow.processed) == 1


async def test_cancellation_before_role_verification_keeps_message_available_for_retry(
    recovery, monkeypatch
):
    flow = recovery
    monkeypatch.setattr(flow.service, "start", lambda: None)
    await flow.service.watch_thread(flow.thread, new=True)
    payload = message_payload(
        FIRST, THREAD, "A message before an interrupted role lookup"
    )
    logs = AsyncMock(return_value=[payload])
    monkeypatch.setattr(flow.state.http, "logs_from", logs)
    lookup_started = asyncio.Event()

    async def blocked_lookup(guild, member):
        lookup_started.set()
        await asyncio.Event().wait()

    flow.member_http.side_effect = blocked_lookup
    task = asyncio.create_task(flow.service.poll_once())
    await asyncio.wait_for(lookup_started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert flow.processed == []
    flow.member_http.side_effect = None
    flow.member_http.return_value = member_payload()
    await flow.service.poll_once()
    assert logs.await_args.kwargs["after"] == THREAD
    assert flow.processed == [("create", FIRST, payload["content"])]


@pytest.mark.parametrize(
    "missing_at", ["watch", "poll"], ids=["new-thread", "cached-thread"]
)
async def test_real_sdk_thread_with_missing_guild_is_rebuilt_before_message_parsing(
    recovery, monkeypatch, missing_at
):
    flow = recovery
    monkeypatch.setattr(flow.service, "start", lambda: None)
    flow.member_http.side_effect = lambda guild, member: member_payload(
        member, bot=member == BOT
    )
    if missing_at == "watch":
        flow.thread.guild = None
        await flow.service.watch_thread(flow.thread, new=True)
    else:
        await flow.service.watch_thread(flow.thread, new=True)
        flow.thread.guild = None  # The watched SDK object lost its guild reference.
    payload = message_payload(
        FIRST, THREAD, "A cacheless SDK thread still needs a reply"
    )
    logs = AsyncMock(return_value=[payload])
    monkeypatch.setattr(flow.state.http, "logs_from", logs)
    await flow.service.poll_once()
    assert logs.await_args.kwargs["after"] == THREAD
    assert flow.processed == [("create", FIRST, payload["content"])]
    handled_message = flow.cog._process_message.await_args.args[0]
    assert isinstance(handled_message.channel, discord.Thread)
    assert isinstance(handled_message.guild, discord.Guild)
    assert handled_message.guild.id == GUILD
    assert flow.bot.get_guild(GUILD) is None


@pytest.mark.parametrize(
    "failure",
    [
        "untracked",
        "no-owner-map",
        "foreign-owner",
        "foreign-guild",
        "text-channel",
    ],
)
async def test_only_tracked_bot_owned_active_threads_in_configured_guild_are_polled(
    recovery, monkeypatch, failure
):
    flow = recovery
    data = thread_payload()
    if failure == "untracked":
        flow.cog.conversation_threads.clear()
    elif failure == "no-owner-map":
        flow.cog.conversation_thread_owners.clear()
    elif failure == "foreign-owner":
        data["owner_id"] = str(MEMBER)
    elif failure == "foreign-guild":
        data["guild_id"] = str(GUILD + 1)
    elif failure == "text-channel":
        data["type"] = 0
    flow.state.http.get_channel.return_value = data
    logs = AsyncMock()
    monkeypatch.setattr(flow.state.http, "logs_from", logs)
    await flow.service.poll_once()
    assert flow.processed == []
    logs.assert_not_awaited()
    flow.member_http.assert_not_awaited()


async def test_gateway_recovery_stops_new_history_polls_and_finishes_inflight_request(
    recovery, monkeypatch
):
    flow = recovery
    monkeypatch.setattr(flow.service, "start", lambda: None)
    await flow.service.watch_thread(flow.thread, new=True)
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked_provider(message, **kwargs):
        entered.set()
        await release.wait()
        flow.processed.append(("create", message.id, message.content))

    flow.cog._process_message.side_effect = blocked_provider
    logs = AsyncMock(
        return_value=[message_payload(FIRST, THREAD, "In-flight question")]
    )
    monkeypatch.setattr(flow.state.http, "logs_from", logs)
    task = asyncio.create_task(flow.service.poll_once())
    await asyncio.wait_for(entered.wait(), timeout=1)
    flow.state._guilds[GUILD] = flow.guild  # A real gateway GUILD_CREATE has arrived.
    release.set()
    await asyncio.wait_for(task, timeout=1)
    assert flow.processed == [("create", FIRST, "In-flight question")]
    logs.reset_mock()
    await flow.service.poll_once()
    logs.assert_not_awaited()


async def test_start_is_idempotent_and_close_stops_background_polling(recovery):
    flow = recovery
    flow.service.start()
    first_task = flow.service._task
    flow.service.start()
    assert flow.service._task is first_task
    await flow.service.close()
    assert first_task.done()
    await flow.service.dispatch(flow.message(), source="rest")
    assert flow.processed == []


@pytest.mark.parametrize("value", ["0", "-1", "not-a-guild", f"{GUILD},", "true"])
async def test_invalid_recovery_configuration_fails_closed(monkeypatch, value):
    from services.conversation_recovery_service import configured_guilds

    monkeypatch.setenv("CONVERSATION_RECOVERY_GUILD_IDS", value)
    with pytest.raises(ValueError, match="positive guild IDs"):
        configured_guilds()


async def test_recovery_requires_explicit_role_and_guild_restrictions(
    recovery, monkeypatch
):
    flow = recovery
    monkeypatch.delenv("BOT_ACCESS_ROLE_IDS")
    await flow.service.dispatch(flow.message(), source="rest")
    assert flow.processed == []
    flow.member_http.assert_not_awaited()
    monkeypatch.setenv("BOT_ACCESS_ROLE_IDS", str(MAINTAINER))
    flow.service.guild_ids = frozenset()
    await flow.service.dispatch(flow.message(), source="rest")
    assert flow.processed == []
    flow.member_http.assert_not_awaited()


def partial_context(flow):
    interaction = discord.Interaction(
        state=flow.state,
        data={
            "id": str(FIRST + 100),
            "type": 2,
            "application_id": str(BOT),
            "token": "offline-interaction-token",
            "version": 1,
            "guild_id": str(GUILD),
            "channel_id": str(PARENT),
            "member": member_payload(),
            "data": {"id": str(FIRST + 101), "name": "gpt", "type": 1},
        },
    )
    return discord.ApplicationContext(flow.bot, interaction)


async def test_cacheless_slash_context_gets_real_rest_objects_without_gateway_cache(
    recovery, monkeypatch
):
    flow = recovery
    ctx = partial_context(flow)
    assert ctx.guild is None
    assert isinstance(ctx.channel, discord.PartialMessageable)
    roles = guild_payload()["roles"]
    parent = guild_payload()["channels"][0]
    monkeypatch.setattr(flow.state.http, "get_roles", AsyncMock(return_value=roles))
    flow.state.http.get_channel.return_value = parent
    flow.member_http.side_effect = lambda guild, member: member_payload(
        member, roles=[BOT_ROLE] if member == BOT else [MAINTAINER], bot=member == BOT
    )
    start = SimpleNamespace(calls=0)

    def start_without_background_task():
        start.calls += 1

    monkeypatch.setattr(flow.service, "start", start_without_background_task)
    assert await flow.service.prepare_context(ctx) is True
    assert isinstance(ctx.guild, discord.Guild) and ctx.guild.id == GUILD
    assert isinstance(ctx.channel, discord.TextChannel) and ctx.channel.id == PARENT
    assert isinstance(ctx.user, discord.Member) and ctx.user.id == MEMBER
    assert ctx.user._roles.has(MAINTAINER)
    assert ctx.guild.me.id == BOT
    assert flow.bot.get_guild(GUILD) is None
    assert start.calls == 1


@pytest.mark.parametrize(
    "failure", ["revoked-role", "wrong-guild", "wrong-member", "dm-channel"]
)
async def test_rest_context_validation_rejects_untrusted_payload_before_state_changes(
    recovery, monkeypatch, failure
):
    flow = recovery
    ctx = partial_context(flow)
    original_channel, original_user = ctx.channel, ctx.user
    parent = guild_payload()["channels"][0]
    authorized = member_payload()
    if failure == "revoked-role":
        authorized["roles"] = [str(CLONE)]
    elif failure == "wrong-guild":
        parent["guild_id"] = str(GUILD + 1)
    elif failure == "wrong-member":
        authorized["user"]["id"] = str(MEMBER + 1)
    elif failure == "dm-channel":
        parent["type"] = 1
    monkeypatch.setattr(
        flow.state.http, "get_roles", AsyncMock(return_value=guild_payload()["roles"])
    )
    flow.state.http.get_channel.return_value = parent
    flow.member_http.side_effect = lambda guild, member: (
        member_payload(BOT, bot=True) if member == BOT else authorized
    )
    assert await flow.service.prepare_context(ctx) is False
    assert ctx.guild is None
    assert ctx.channel is original_channel and ctx.user is original_user
    assert flow.bot.get_guild(GUILD) is None
    assert flow.processed == []
    assert flow.service._task is None


async def test_ready_reconnect_keeps_active_state_and_one_poll_and_pickle_loop(
    conversation, monkeypatch, tmp_path
):
    flow = conversation
    directory = tmp_path / "pickles"
    directory.mkdir()
    for name in (
        "full_conversation_history",
        "conversation_threads",
        "conversation_thread_owners",
        "instructions",
    ):
        (directory / f"{name}.pickle").write_bytes(pickle.dumps({}))
    flow.cog.conversation_recovery.guild_ids = frozenset({GUILD})
    entered, release = asyncio.Event(), asyncio.Event()
    actual_sleep = asyncio.sleep

    async def controlled_sleep(delay):
        if delay == 15:
            entered.set()
            await release.wait()
        else:
            await actual_sleep(3600)

    monkeypatch.setattr(flow.bot, "register_commands", AsyncMock())
    with monkeypatch.context() as patch:
        patch.setattr(asyncio, "sleep", controlled_sleep)
        ready = asyncio.create_task(flow.cog.on_ready())
        try:
            await asyncio.wait_for(entered.wait(), timeout=1)
            first_poll = flow.cog.conversation_recovery._task
            sentinel = SimpleNamespace(model="gpt-6.1-sol", history=["In-memory turn"])
            flow.cog.conversation_threads[THREAD] = sentinel
            await asyncio.wait_for(flow.cog.on_ready(), timeout=1)
            assert flow.cog.conversation_threads[THREAD] is sentinel
            assert flow.cog.conversation_recovery._task is first_poll
            assert flow.cog._pickle_loop_running is True
            assert flow.cog.pickle_queue.empty()
        finally:
            ready.cancel()
            await asyncio.gather(ready, return_exceptions=True)


async def test_cacheless_converse_creates_real_thread_and_accepts_recovered_message(
    conversation, monkeypatch
):
    flow = conversation
    flow.state._guilds.clear()
    flow.cog.conversation_recovery.guild_ids = frozenset({GUILD})
    monkeypatch.setattr(flow.cog.conversation_recovery, "start", lambda: None)
    data = guild_payload()
    roles = data["roles"]
    for role in roles:
        role["permissions"] = str(
            flow.guild.get_role(int(role["id"])).permissions.value
        )
    parent = data["channels"][0]
    monkeypatch.setattr(flow.state.http, "get_guild", AsyncMock(return_value=data))
    monkeypatch.setattr(flow.state.http, "get_roles", AsyncMock(return_value=roles))
    monkeypatch.setattr(flow.state.http, "get_channel", AsyncMock(return_value=parent))
    monkeypatch.setattr(
        flow.state.http,
        "get_member",
        AsyncMock(
            side_effect=lambda guild, member: member_payload(
                member,
                roles=[BOT_ROLE] if member == BOT else [MAINTAINER],
                bot=member == BOT,
            )
        ),
    )
    channel = await flow.start()
    flow.ctx.defer.assert_awaited_once_with(ephemeral=True)
    assert isinstance(flow.ctx.channel, discord.TextChannel)
    assert isinstance(flow.ctx.guild, discord.Guild)
    assert flow.bot.get_guild(GUILD) is None
    flow.answers.append("An answer recovered without gateway guild membership.")
    thread = flow.guild.get_thread(channel)
    message = discord.Message(
        state=flow.state,
        channel=thread,
        data=message_payload(channel + 1, channel, "Recover this new thread message"),
    )
    await flow.cog.conversation_recovery.dispatch(message, source="rest")
    assert len(flow.provider_calls) == 1
    assert flow.provider_calls[0]["json"]["model"] == "gpt-6.1-sol"
    answer = "An answer recovered without gateway guild membership."
    assert answer in flow.cog.conversation_threads[channel].history[-1].text
    assert any(message["content"] == answer for message in flow.sent)
