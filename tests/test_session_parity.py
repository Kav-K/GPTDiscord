"""Full-channel startup and optional routing through actual conversation handlers."""

from unittest.mock import AsyncMock

import discord
from discord.webhook.async_ import async_context
import pytest

from tests.test_conversation_flow import (  # noqa: F401
    GUILD, MEMBER, PARENT, conversation, end_context, member_payload, message_payload,
)
from tests.test_conversation_recovery import guild_payload
from tests.test_text_features import TextAdapter


async def start_channel(flow):
    await flow.cog.converse_command(
        flow.ctx, draw=False, opener=None, opener_file=None, private=False,
        minimal=True, model="gpt-6.1-sol", temperature=None, top_p=None,
        frequency_penalty=None, presence_penalty=None, use_threads=False,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["index", "search", "code"])
async def test_optional_chat_mentions_do_not_also_trigger_generic_gpt(conversation, monkeypatch, route):
    flow = conversation
    flow.cog.conversation_recovery.register_route(
        route, channels=lambda: {PARENT: object()},
        owner_id=lambda channel_id: MEMBER, handler=AsyncMock(),
    )
    generic = AsyncMock()
    monkeypatch.setattr(flow.cog, "_process_message", generic)
    monkeypatch.setattr(flow.module, "BOT_TAGGABLE", True)
    await flow.deliver(f"<@{flow.bot.user.id}> summarize this data", channel=PARENT)
    generic.assert_not_awaited()
    assert not flow.provider_calls


@pytest.mark.asyncio
async def test_full_channel_cannot_overlap_an_optional_thread(conversation, monkeypatch):
    flow = conversation
    thread_id = await flow.start()
    flow.ctx.channel = flow.guild.get_thread(thread_id)
    flow.cog.conversation_threads.clear()
    monkeypatch.setattr(flow.module, "CHANNEL_CHAT_ROLES", [None])
    await start_channel(flow)
    assert not flow.cog.conversation_threads
    assert "server text channel" in flow.ctx.respond.await_args.args[0]
    assert flow.ctx.respond.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_full_channel_command_enrolls_rest_recovery_without_gateway(conversation, monkeypatch):
    flow = conversation
    recovery = flow.cog.conversation_recovery
    recovery.guild_ids = frozenset({GUILD})
    monkeypatch.setattr(recovery, "start", lambda: None)
    monkeypatch.setattr(flow.module, "CHANNEL_CHAT_ROLES", [None])
    flow.state._guilds.clear()
    flow.ctx = end_context(flow, PARENT)
    assert flow.ctx.guild is None
    assert isinstance(flow.ctx.channel, discord.PartialMessageable)
    parent = {**guild_payload()["channels"][0], "guild_id": str(GUILD)}
    monkeypatch.setattr(flow.state.http, "get_channel", AsyncMock(return_value=parent))
    monkeypatch.setattr(flow.state.http, "get_guild", AsyncMock(return_value=guild_payload()))
    monkeypatch.setattr(flow.state.http, "get_roles", AsyncMock(return_value=guild_payload()["roles"]))
    monkeypatch.setattr(flow.state.http, "get_member", AsyncMock(side_effect=lambda guild, user: member_payload(user, bot=user == flow.bot.user.id)))
    baseline = PARENT + 1000
    pending = []

    async def logs(channel, limit, *, after=None, **kwargs):
        assert channel == PARENT
        return [{"id": str(baseline)}] if after is None else list(pending)

    monkeypatch.setattr(flow.state.http, "logs_from", AsyncMock(side_effect=logs))
    adapter = TextAdapter(flow, PARENT)
    token = async_context.set(adapter)
    try:
        await start_channel(flow)
    finally:
        async_context.reset(token)
    assert PARENT in flow.cog.conversation_threads
    assert recovery._cursor(PARENT) == baseline
    assert recovery._full_channels == {PARENT: GUILD}
    assert adapter.initial[flow.ctx.interaction.id] == {"type": 5, "data": {"flags": 64}}
    assert isinstance(flow.ctx.channel, discord.TextChannel)
    flow.answers.append("The recovered channel answer.")
    pending.append(message_payload(baseline + 1, PARENT, "A full-channel turn."))
    await recovery.poll_once()
    assert len(flow.provider_calls) == 1
    assert flow.cog.full_conversation_history[PARENT] == ["A full-channel turn.", "The recovered channel answer."]
    await recovery.poll_once()
    assert len(flow.provider_calls) == 1
    assert flow.provider_calls[0]["json"]["model"] == "gpt-6.1-sol"
