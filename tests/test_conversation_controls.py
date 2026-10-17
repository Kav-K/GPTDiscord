"""Conversation controls use current turn state and one private acknowledgement.

The application handlers, SDK interactions and conversation setup are real; only
Discord/OpenAI HTTP boundaries and explicitly identified provider work are faked.
"""

import asyncio
from copy import deepcopy
import importlib
from itertools import count
from unittest.mock import AsyncMock

import discord
from discord.ext import pages
from discord.webhook.async_ import async_context
import pytest

from tests.test_conversation_flow import (
    BOT,
    GUILD,
    MEMBER,
    PARENT,
    conversation,
    member_payload,
    message_payload,
)

CONTROL_IDS = count(1600000000000000000)
FOLLOWUP_IDS = count(1700000000000000000)


def component_interaction(
    flow, response_message, *, user_id=MEMBER, channel_id=None, identifier=None
):
    """Construct an actual SDK component, including the missing-guild fallback."""
    channel_id = response_message.channel.id if channel_id is None else channel_id
    identifier = next(CONTROL_IDS) if identifier is None else identifier
    data = deepcopy(flow.messages[response_message.id])
    data["channel_id"] = str(channel_id)
    return discord.Interaction(
        state=flow.state,
        data={
            "id": str(identifier),
            "type": 3,
            "application_id": str(BOT),
            "token": f"offline-component-{identifier}-{channel_id}",
            "version": 1,
            "guild_id": str(GUILD),
            "channel_id": str(channel_id),
            "member": member_payload(user_id),
            "message": data,
            "data": {"custom_id": "conversation_redo", "component_type": 2},
        },
    )


class StrictInteractionAdapter:
    """Exercise the SDK's real response state with a strict HTTP boundary."""

    def __init__(self):
        self.initial = {}
        self.followups = []

    async def create_interaction_response(
        self, identifier, token, *, type, data=None, **kwargs
    ):
        assert (
            identifier not in self.initial
        ), "An interaction received two initial responses"
        assert token.startswith(f"offline-component-{identifier}-")
        self.initial[identifier] = {"type": type, "data": deepcopy(data or {})}
        return {"interaction": {"id": str(identifier)}}

    async def execute_webhook(self, identifier, token, *, payload, **kwargs):
        interaction_id, channel_id = map(int, token.rsplit("-", 2)[-2:])
        assert (
            interaction_id in self.initial
        ), "Followup sent before an initial response"
        self.followups.append((interaction_id, deepcopy(payload)))
        data = message_payload(
            next(FOLLOWUP_IDS), channel_id, payload.get("content", ""), bot=True
        )
        data["webhook_id"] = str(identifier)
        data["flags"] = payload.get("flags", 0)
        return data


@pytest.fixture
async def controls(conversation):
    """Start and answer a real turn so bind_turn records its actual tail objects."""
    flow = conversation
    flow.text = importlib.import_module("services.text_service")
    flow.thread_id = await flow.start()
    flow.answers.append("The original completed answer.")
    flow.input_id = await flow.deliver("My original question.", channel=flow.thread_id)
    flow.thread = flow.cog.conversation_threads[flow.thread_id]
    flow.redo = flow.cog.redo_users[MEMBER]
    assert flow.redo.conversation_thread is flow.thread
    assert flow.redo.conversation_tail == tuple(flow.thread.history[-2:])
    assert flow.text.TextService.current_turn(flow.cog, flow.redo, flow.thread_id)
    flow.adapter = StrictInteractionAdapter()
    context = async_context.set(flow.adapter)

    def retry():
        # A stale credential/model in an old view must not override current state.
        return flow.text.RedoButton(
            flow.cog,
            model="gpt-4o-mini",
            from_ask_command=False,
            from_edit_command=False,
            custom_api_key="obsolete-view-key",
        )

    flow.retry = retry
    flow.click = lambda **kwargs: component_interaction(
        flow, flow.redo.response, **kwargs
    )
    try:
        yield flow
    finally:
        async_context.reset(context)


def assert_private_initial(flow, interaction):
    assert interaction.response.is_done()
    response = flow.adapter.initial[interaction.id]
    assert response["type"] in (4, 5)
    assert response["data"]["flags"] & 64
    return response["data"].get("content", "")


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["rotated-personal-key", None, ""])
async def test_retry_resolves_current_personal_key_before_provider_work(
    controls, monkeypatch, key
):
    flow = controls
    monkeypatch.setattr(flow.module, "USER_INPUT_API_KEYS", True)
    monkeypatch.setattr(
        flow.module, "USER_KEY_DB", {} if key is None else {MEMBER: key}
    )
    generate = AsyncMock(return_value=True)
    monkeypatch.setattr(flow.text.TextService, "encapsulated_send", generate)
    interaction = flow.click()

    await flow.retry().callback(interaction)

    content = assert_private_initial(flow, interaction)
    if key:
        generate.assert_awaited_once()
        assert generate.await_args.kwargs["custom_api_key"] == key
        assert generate.await_args.kwargs["model"] == flow.thread.model
        assert generate.await_args.kwargs["id"] == flow.thread_id
    else:
        generate.assert_not_awaited()
        assert "/setup" in content
    assert not flow.cog.awaiting_responses
    assert not flow.cog.awaiting_thread_responses


@pytest.mark.asyncio
async def test_retry_uses_same_lock_as_gateway_and_rest_ingress(controls, monkeypatch):
    flow = controls
    generate = AsyncMock()
    monkeypatch.setattr(flow.text.TextService, "encapsulated_send", generate)
    lock = flow.cog.conversation_recovery.lock_for(flow.thread_id)
    assert flow.text.TextService.conversation_lock(flow.cog, flow.thread_id) is lock
    interaction = flow.click()

    async with lock:
        await flow.retry().callback(interaction)

    generate.assert_not_awaited()
    assert "wait" in assert_private_initial(flow, interaction).lower()
    assert not flow.cog.awaiting_responses
    assert not flow.cog.awaiting_thread_responses


@pytest.mark.asyncio
async def test_concurrent_retry_and_end_do_not_start_second_generation(
    controls, monkeypatch
):
    flow = controls
    entered, finish = asyncio.Event(), asyncio.Event()

    async def generation(*args, **kwargs):
        assert flow.cog.conversation_recovery.lock_for(flow.thread_id).locked()
        entered.set()
        await finish.wait()

    generate = AsyncMock(side_effect=generation)
    monkeypatch.setattr(flow.text.TextService, "encapsulated_send", generate)
    end = AsyncMock()
    monkeypatch.setattr(flow.cog, "end_conversation", end)
    first, duplicate, closing = flow.click(), flow.click(), flow.click()
    task = asyncio.create_task(flow.retry().callback(first))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await flow.retry().callback(duplicate)
        await flow.text.EndConvoButton(flow.cog).callback(closing)
        generate.assert_awaited_once()
        end.assert_not_awaited()
        assert "wait" in assert_private_initial(flow, duplicate).lower()
        assert "wait" in assert_private_initial(flow, closing).lower()
    finally:
        finish.set()
        await asyncio.wait_for(task, 1)
    assert_private_initial(flow, first)
    assert not flow.cog.awaiting_responses
    assert not flow.cog.awaiting_thread_responses


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stale",
    ["ended", "replaced", "cross-thread", "latest-tail", "response-id", "other-author"],
)
async def test_stale_retry_controls_never_reach_provider(controls, monkeypatch, stale):
    flow = controls
    from models.user_model import EmbeddedConversationItem, Thread

    generate = AsyncMock()
    monkeypatch.setattr(flow.text.TextService, "encapsulated_send", generate)
    if stale == "ended":
        flow.cog.conversation_threads.pop(flow.thread_id)
    elif stale == "replaced":
        flow.cog.conversation_threads[flow.thread_id] = Thread(flow.thread_id)
    elif stale == "latest-tail":
        flow.thread.history.append(EmbeddedConversationItem("A newer turn", 0))
    interaction = flow.click(
        channel_id=flow.thread_id + 999 if stale == "cross-thread" else None,
        user_id=MEMBER + 1 if stale == "other-author" else MEMBER,
    )
    if stale == "response-id":
        flow.redo.interactions.clear()

    await flow.retry().callback(interaction)

    generate.assert_not_awaited()
    assert_private_initial(flow, interaction)
    assert len(flow.provider_calls) == 1  # Only fixture setup's original answer.


@pytest.mark.asyncio
async def test_retry_from_cacheless_component_uses_bound_local_conversation(
    controls, monkeypatch
):
    flow = controls
    flow.state._guilds.clear()
    interaction = flow.click()
    assert isinstance(interaction.channel, discord.PartialMessageable)
    generate = AsyncMock(return_value=True)
    monkeypatch.setattr(flow.text.TextService, "encapsulated_send", generate)

    await flow.retry().callback(interaction)

    generate.assert_awaited_once()
    assert generate.await_args.kwargs["ctx"] is flow.redo.ctx
    assert isinstance(generate.await_args.kwargs["ctx"].channel, discord.Thread)
    assert flow.state._guilds == {}
    assert_private_initial(flow, interaction)


@pytest.mark.asyncio
async def test_end_error_after_ack_uses_sanitized_followup_once(
    controls, monkeypatch, capsys
):
    flow = controls
    interaction = flow.click()

    async def fail(*args, **kwargs):
        assert interaction.response.is_done(), "End must acknowledge before REST work"
        raise RuntimeError("SENSITIVE upstream exception body")

    monkeypatch.setattr(flow.cog, "end_conversation", AsyncMock(side_effect=fail))

    await flow.text.EndConvoButton(flow.cog).callback(interaction)

    assert_private_initial(flow, interaction)
    assert len(flow.adapter.followups) == 1
    identifier, payload = flow.adapter.followups[0]
    assert identifier == interaction.id
    assert payload["flags"] & 64
    assert "SENSITIVE" not in payload["content"]
    assert "SENSITIVE" not in capsys.readouterr().out
    assert "couldn't" in payload["content"].lower()
    assert not flow.cog.conversation_recovery.lock_for(flow.thread_id).locked()


@pytest.mark.asyncio
async def test_end_busy_thread_uses_initial_private_message_without_closing(
    controls, monkeypatch
):
    flow = controls
    flow.cog.awaiting_thread_responses.append(flow.thread_id)
    end = AsyncMock()
    monkeypatch.setattr(flow.cog, "end_conversation", end)
    interaction = flow.click()

    await flow.text.EndConvoButton(flow.cog).callback(interaction)

    end.assert_not_awaited()
    assert "wait" in assert_private_initial(flow, interaction).lower()
    assert not flow.adapter.followups
    assert flow.thread_id in flow.cog.conversation_threads


@pytest.mark.asyncio
async def test_shutdown_drains_an_active_retry_before_closing(controls, monkeypatch):
    flow = controls
    entered, finish = asyncio.Event(), asyncio.Event()

    async def generation(*args, **kwargs):
        assert asyncio.current_task() in flow.cog.conversation_recovery._active
        entered.set()
        await finish.wait()

    monkeypatch.setattr(flow.text.TextService, "encapsulated_send", generation)
    retry = asyncio.create_task(flow.retry().callback(flow.click()))
    await asyncio.wait_for(entered.wait(), 1)
    closing = asyncio.create_task(flow.cog.conversation_recovery.close())
    try:
        await asyncio.sleep(0)
        assert not closing.done()
    finally:
        finish.set()
        await asyncio.wait_for(asyncio.gather(retry, closing), 1)
    assert not flow.cog.conversation_recovery._active
    assert flow.cog.awaiting_responses == flow.cog.awaiting_thread_responses == []


@pytest.mark.asyncio
async def test_retry_during_shutdown_never_starts_provider(controls, monkeypatch):
    flow = controls
    await flow.cog.conversation_recovery.close()
    generate = AsyncMock()
    monkeypatch.setattr(flow.text.TextService, "encapsulated_send", generate)
    interaction = flow.click()

    await flow.retry().callback(interaction)

    generate.assert_not_awaited()
    assert_private_initial(flow, interaction)
    assert len(flow.adapter.followups) == 1
    assert "shutting down" in flow.adapter.followups[0][1]["content"]
    assert flow.cog.awaiting_responses == flow.cog.awaiting_thread_responses == []


@pytest.mark.asyncio
async def test_private_retry_cannot_target_public_redo_replaced_during_provider_await(
    controls, monkeypatch
):
    """A concurrent public answer must never receive a private retry's result."""
    flow = controls
    from models.openai_model import Override

    private_redo = flow.redo
    private_redo.ctx.channel._type = discord.ChannelType.private_thread
    assert private_redo.ctx.channel.is_private()
    assert private_redo.response.channel.is_private()
    private_history = list(flow.thread.history)
    private_full_history = list(flow.cog.full_conversation_history[flow.thread_id])
    entered, finish = asyncio.Event(), asyncio.Event()

    async def private_generation(*args, **kwargs):
        entered.set()
        await finish.wait()
        return {"choices": [{"message": {"content": "PRIVATE replacement answer."}}]}

    private_generate = AsyncMock(side_effect=private_generation)
    monkeypatch.setattr(flow.model, "send_chatgpt_chat_request", private_generate)
    public_generate = AsyncMock(
        return_value={"choices": [{"message": {"content": "Public answer only."}}]}
    )
    monkeypatch.setattr(flow.model, "send_request", public_generate)
    public_ctx = discord.Message(
        state=flow.state,
        channel=flow.guild.get_channel(PARENT),
        data=message_payload(next(CONTROL_IDS), PARENT, "An unrelated public request."),
    )
    interaction = flow.click()
    private_task = asyncio.create_task(flow.retry().callback(interaction))
    public_paginator = None
    try:
        await asyncio.wait_for(entered.wait(), 1)
        # Complete the actual standalone /ask response path while the private
        # retry is waiting. This replaces the same author's global redo entry.
        await flow.text.TextService.encapsulated_send(
            flow.cog,
            id=MEMBER,
            prompt=public_ctx.content,
            ctx=public_ctx,
            overrides=Override(),
            model=flow.thread.model,
            from_ask_command=True,
        )
        public_redo = flow.cog.redo_users[MEMBER]
        assert public_redo is not private_redo
        assert public_redo.ctx is public_ctx
        assert public_redo.response.channel.id == PARENT
        assert public_redo.standalone_request is True
        public_paginator = pages.Paginator(pages=["Public answer only."], timeout=None)
        public_paginator.update = AsyncMock()
        public_redo.paginator = public_paginator
        public_redo_state = dict(vars(public_redo))
        public_pages = list(public_paginator.pages)
        public_history = list(flow.cog.full_conversation_history[PARENT])
        public_content = flow.messages[public_redo.response.id]["content"]
    finally:
        finish.set()
        await asyncio.wait_for(private_task, 1)
        if public_paginator is not None:
            public_paginator.stop()

    private_generate.assert_awaited_once()
    public_generate.assert_awaited_once()
    public_paginator.update.assert_not_awaited()
    assert vars(public_redo) == public_redo_state
    assert public_paginator.pages == public_pages
    assert flow.cog.redo_users[MEMBER] is public_redo
    assert flow.messages[public_redo.response.id]["content"] == public_content
    assert "PRIVATE" not in public_content
    assert flow.cog.full_conversation_history[PARENT] == public_history
    assert (
        flow.messages[private_redo.response.id]["content"]
        == "PRIVATE replacement answer."
    )
    assert flow.thread.history[:-1] == private_history[:-1]
    assert "PRIVATE replacement answer." in flow.thread.history[-1].text
    assert flow.cog.full_conversation_history[flow.thread_id] == [
        *private_full_history[:-1],
        "PRIVATE replacement answer.",
    ]
    assert private_redo.conversation_tail == tuple(flow.thread.history[-2:])
    assert_private_initial(flow, interaction)
    assert not flow.cog.awaiting_responses
    assert not flow.cog.awaiting_thread_responses
