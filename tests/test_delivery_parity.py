"""Scoped REST delivery for optional sessions and explicit channel conversations."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from discord.webhook.async_ import async_context
import pytest

from services.conversation_recovery_service import ConversationRecoveryService
from services.request_context import single_attempt_requests
from tests.test_conversation_recovery import (
    BOT,
    FIRST,
    GUILD,
    MEMBER,
    PARENT,
    THREAD,
    guild_payload,
    member_payload,
    message_payload,
    recovery as recovery,
    thread_payload,
)
from tests.test_conversation_flow import conversation as conversation, end_context
from tests.test_text_features import TextAdapter


def register_optional(flow, route="index", handler=None):
    flow.cog.conversation_threads.clear()
    sessions = {THREAD: SimpleNamespace(owner_id=MEMBER)}
    handler = handler or AsyncMock()
    flow.service.register_route(
        route,
        channels=lambda: sessions,
        owner_id=lambda channel_id: getattr(sessions.get(channel_id), "owner_id", None),
        handler=handler,
    )
    return sessions, handler


@pytest.mark.parametrize("route", ["index", "search", "code"])
async def test_optional_thread_has_one_turn_across_rest_and_gateway(
    recovery, monkeypatch, route
):
    flow = recovery
    monkeypatch.setattr(flow.service, "start", lambda: None)
    attempts = []

    async def process(message):
        attempts.append((message.id, single_attempt_requests.get()))
        await asyncio.sleep(0)

    _, handler = register_optional(flow, route, AsyncMock(side_effect=process))
    await flow.service.watch_thread(flow.thread, new=True)
    assert flow.service._cursor(THREAD, route) == THREAD
    logs = AsyncMock(return_value=[message_payload(FIRST, THREAD, "Optional chat")])
    monkeypatch.setattr(flow.state.http, "logs_from", logs)
    await flow.service.poll_once()
    await asyncio.gather(
        flow.service.dispatch(flow.message(), route=route),
        flow.service.dispatch(flow.message(), source="rest", route=route),
    )
    assert attempts == [(FIRST, True)]
    handler.assert_awaited_once()
    flow.cog._process_message.assert_not_awaited()
    assert flow.service._cursor(THREAD, route) == FIRST
    assert flow.service._cursor(THREAD) is None
    assert not single_attempt_requests.get()


async def test_optional_edits_cannot_reinvoke_an_agent(recovery):
    flow = recovery
    _, handler = register_optional(flow)
    flow.member_http.side_effect = lambda guild, member: member_payload(
        member, bot=member == BOT
    )
    await flow.service.dispatch(flow.message(), route="index")
    await flow.service.dispatch(
        flow.message(content="Edited question"), route="index", edited=True
    )
    await flow.service.dispatch(
        flow.message(content="Edited question"), source="rest", route="index"
    )
    handler.assert_awaited_once()


async def test_optional_provider_failure_is_not_retried_after_restart(recovery):
    flow = recovery
    sessions, handler = register_optional(
        flow, handler=AsyncMock(side_effect=RuntimeError("Provider failed"))
    )
    with pytest.raises(RuntimeError, match="Provider failed"):
        await flow.service.dispatch(flow.message(), source="rest", route="index")
    await flow.service.close()
    restarted = ConversationRecoveryService(flow.cog, flow.data_path, guild_ids=[GUILD])
    restarted.register_route(
        "index",
        channels=lambda: sessions,
        owner_id=lambda channel_id: sessions[channel_id].owner_id,
        handler=handler,
    )
    try:
        await restarted.dispatch(flow.message(), route="index")
        handler.assert_awaited_once()
    finally:
        await restarted.close()


async def test_optional_first_enrollment_skips_backlog_and_keeps_its_own_cursor(
    recovery, monkeypatch
):
    flow = recovery
    _, handler = register_optional(flow)
    flow.member_http.side_effect = lambda guild, member: member_payload(
        member, bot=member == BOT
    )
    flow.service._save_cursor(THREAD, GUILD, FIRST + 100)
    logs = AsyncMock(return_value=[message_payload(FIRST, THREAD, "Before enrollment")])
    monkeypatch.setattr(flow.state.http, "logs_from", logs)
    await flow.service.poll_once()
    assert flow.service._cursor(THREAD, "index") == FIRST
    handler.assert_not_awaited()
    logs.return_value = [message_payload(FIRST + 1, THREAD, "After enrollment")]
    await flow.service.poll_once()
    handler.assert_awaited_once()
    assert flow.service._cursor(THREAD, "index") == FIRST + 1
    assert flow.service._cursor(THREAD) == FIRST + 100


@pytest.mark.parametrize("failure", ["owner-removed", "owner-malformed", "foreign-thread"])
async def test_optional_thread_registration_and_bot_ownership_are_required(
    recovery, monkeypatch, failure
):
    flow = recovery
    sessions, handler = register_optional(flow)
    if failure == "owner-removed":
        sessions.clear()
    elif failure == "owner-malformed":
        sessions[THREAD].owner_id = True
    else:
        flow.state.http.get_channel.return_value = thread_payload(owner=MEMBER)
    logs = AsyncMock()
    monkeypatch.setattr(flow.state.http, "logs_from", logs)
    await flow.service.poll_once()
    logs.assert_not_awaited()
    handler.assert_not_awaited()


async def test_optional_route_removed_during_fresh_role_lookup_is_not_claimed(recovery):
    flow = recovery
    sessions, handler = register_optional(flow)
    original = flow.member_http.return_value

    async def remove_session(*args):
        sessions.clear()
        return original

    flow.member_http.side_effect = remove_session
    await flow.service.dispatch(flow.message(), source="rest", route="index")
    handler.assert_not_awaited()
    sessions[THREAD] = SimpleNamespace(owner_id=MEMBER)
    flow.member_http.side_effect = None
    await flow.service.dispatch(flow.message(), source="rest", route="index")
    handler.assert_awaited_once()


async def test_optional_membership_revocation_is_checked_before_claim(recovery):
    flow = recovery
    _, handler = register_optional(flow)
    flow.member_http.return_value = member_payload(roles=[])
    await flow.service.dispatch(flow.message(), source="rest", route="index")
    handler.assert_not_awaited()
    flow.member_http.return_value = member_payload()
    await flow.service.dispatch(flow.message(), source="rest", route="index")
    handler.assert_awaited_once()


async def test_ambiguous_route_registry_fails_closed_before_claim(recovery):
    flow = recovery
    sessions = {THREAD: MEMBER}
    handler = AsyncMock()
    flow.service.register_route(
        "index", channels=lambda: sessions, owner_id=sessions.get, handler=handler
    )
    await flow.service.dispatch(flow.message(), source="rest", route="index")
    await flow.service.dispatch(flow.message(), source="rest")
    handler.assert_not_awaited()
    flow.cog._process_message.assert_not_awaited()
    flow.cog.conversation_threads.clear()
    await flow.service.dispatch(flow.message(), source="rest", route="index")
    handler.assert_awaited_once()


def channel_conversation(flow):
    channel = flow.guild.get_channel(PARENT)
    flow.cog.conversation_threads.clear()
    flow.cog.conversation_threads[PARENT] = SimpleNamespace(history=[])
    flow.cog.conversation_thread_owners[MEMBER] = [PARENT]
    flow.state.http.get_channel.return_value = guild_payload()["channels"][0]
    return channel


async def test_full_channel_requires_explicit_enrollment_and_skips_old_messages(
    recovery, monkeypatch
):
    flow = recovery
    channel = channel_conversation(flow)
    monkeypatch.setattr(flow.service, "start", lambda: None)
    old = message_payload(FIRST, PARENT, "Old channel history")
    new = message_payload(FIRST + 1, PARENT, "New channel conversation turn")
    logs = AsyncMock(return_value=[old])
    monkeypatch.setattr(flow.state.http, "logs_from", logs)
    message = discord.Message(state=flow.state, channel=channel, data=deepcopy(new))
    await flow.service.poll_once()
    await flow.service.dispatch(message, source="rest")
    logs.assert_not_awaited()
    flow.cog._process_message.assert_not_awaited()
    await flow.service.watch_channel(channel, new=True)
    assert flow.service._cursor(PARENT) == FIRST
    assert flow.service._full_channels == {PARENT: GUILD}
    logs.return_value = [old, new]
    await flow.service.poll_once()
    assert flow.processed == [("create", FIRST + 1, new["content"])]
    assert flow.cog._process_message.call_args.kwargs == {"conversation_only": True}
    await flow.service.dispatch(message)
    assert len(flow.processed) == 1


async def test_full_channel_restart_keeps_enrollment_and_new_session_rebaselines(
    recovery, monkeypatch
):
    flow = recovery
    flow.member_http.side_effect = lambda guild, member: member_payload(
        member, bot=member == BOT
    )
    channel = channel_conversation(flow)
    monkeypatch.setattr(flow.service, "start", lambda: None)
    logs = AsyncMock(return_value=[message_payload(FIRST, PARENT, "Old channel history")])
    monkeypatch.setattr(flow.state.http, "logs_from", logs)
    await flow.service.watch_channel(channel, new=True)
    await flow.service.close()
    restarted = ConversationRecoveryService(flow.cog, flow.data_path, guild_ids=[GUILD])
    monkeypatch.setattr(restarted, "start", lambda: None)
    try:
        logs.return_value = [message_payload(FIRST + 1, PARENT, "While offline")]
        await restarted.poll_once()
        assert flow.processed == [("create", FIRST + 1, "While offline")]
        logs.return_value = [message_payload(FIRST + 2, PARENT, "Between sessions")]
        await restarted.watch_channel(channel, new=True)
        assert restarted._cursor(PARENT) == FIRST + 2
        await restarted.poll_once()
        assert len(flow.processed) == 1
        flow.cog.conversation_threads.clear()
        logs.reset_mock()
        await restarted.poll_once()
        logs.assert_not_awaited()
    finally:
        await restarted.close()


async def test_explicit_channel_can_enroll_before_gateway_loss(recovery, monkeypatch):
    flow = recovery
    channel = channel_conversation(flow)
    flow.state._guilds[GUILD] = flow.guild
    monkeypatch.setattr(flow.service, "start", lambda: None)
    logs = AsyncMock(return_value=[message_payload(FIRST, PARENT, "Before enrollment")])
    monkeypatch.setattr(flow.state.http, "logs_from", logs)
    await flow.service.watch_channel(channel, new=True)
    assert flow.service._cursor(PARENT) == FIRST
    logs.reset_mock()
    await flow.service.poll_once()
    logs.assert_not_awaited()
    new = message_payload(FIRST + 1, PARENT, "After gateway loss")
    logs.return_value = [new]
    flow.state._guilds.pop(GUILD)
    await flow.service.poll_once()
    assert flow.processed == [("create", FIRST + 1, "After gateway loss")]
    flow.state._guilds[GUILD] = flow.guild
    message = discord.Message(state=flow.state, channel=channel, data=deepcopy(new))
    await flow.service.dispatch(message)
    assert len(flow.processed) == 1
    logs.reset_mock()
    await flow.service.poll_once()
    logs.assert_not_awaited()


async def test_optional_routes_cannot_enroll_normal_channels(recovery, monkeypatch):
    flow = recovery
    channel = channel_conversation(flow)
    flow.cog.conversation_threads.clear()
    sessions = {PARENT: MEMBER}
    handler = AsyncMock()
    flow.service.register_route(
        "code", channels=lambda: sessions, owner_id=sessions.get, handler=handler
    )
    logs = AsyncMock()
    monkeypatch.setattr(flow.state.http, "logs_from", logs)
    await flow.service.watch_channel(channel, new=True)
    await flow.service.poll_once()
    assert flow.service._full_channels == {}
    logs.assert_not_awaited()
    handler.assert_not_awaited()


@pytest.mark.parametrize("failure", [None, "no-role", "unconfigured", "listing-denied"])
async def test_cacheless_thread_cleanup_lists_only_verified_bot_owned_active_threads(
    conversation, monkeypatch, failure
):
    flow = conversation
    flow.state._guilds.clear()
    recovery = flow.cog.conversation_recovery
    recovery.guild_ids = frozenset([GUILD]) if failure != "unconfigured" else frozenset()
    monkeypatch.setattr(recovery, "start", lambda: None)
    monkeypatch.setattr(flow.state.http, "get_guild", AsyncMock(return_value=guild_payload()))
    monkeypatch.setattr(flow.state.http, "get_roles", AsyncMock(return_value=guild_payload()["roles"]))
    monkeypatch.setattr(flow.state.http, "get_channel", AsyncMock(return_value=guild_payload()["channels"][0]))

    def fresh_member(guild, identifier):
        if failure == "no-role" and identifier != BOT:
            return member_payload(identifier, roles=[])
        return member_payload(identifier, bot=identifier == BOT)

    monkeypatch.setattr(flow.state.http, "get_member", AsyncMock(side_effect=fresh_member))
    listing = []
    for offset, name, owner, kind, archived, guild in [
        (0, "A conversation with GPT", BOT, 11, False, GUILD),
        (1, "Closed-GPT", BOT, 12, False, GUILD),
        (2, "A conversation with GPT", MEMBER, 11, False, GUILD),
        (3, "Ordinary notes", BOT, 11, False, GUILD),
        (4, "Closed-GPT", BOT, 0, False, GUILD),
        (5, "Closed-GPT", BOT, 11, True, GUILD),
        (6, "Closed-GPT", BOT, 11, False, GUILD + 1),
    ]:
        payload = thread_payload(THREAD + offset, owner=owner, guild=guild, archived=archived)
        payload.update(name=name, type=kind)
        listing.append(payload)
    active = AsyncMock(return_value={"threads": listing, "members": []})
    if failure == "listing-denied":
        active.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), {"code": 50001, "message": "Missing Access"})
    monkeypatch.setattr(flow.state.http, "get_active_threads", active)
    deletion = AsyncMock()
    monkeypatch.setattr(flow.state.http, "delete_channel", deletion)
    ctx = end_context(flow, PARENT)
    adapter = TextAdapter(flow, PARENT)
    token = async_context.set(adapter)
    try:
        await flow.cog.delete_all_conversation_threads_command(ctx)
    finally:
        async_context.reset(token)
    if failure:
        deletion.assert_not_awaited()
        assert "No threads were deleted" in adapter.followups[-1]["content"]
    else:
        assert [call.args[0] for call in deletion.await_args_list] == [THREAD, THREAD + 1]
        assert "Deleted 2 active" in adapter.followups[-1]["content"]
    assert adapter.followups[-1]["flags"] & 64
    assert not flow.provider_calls
