"""Turn replacements must preserve shared conversation memory and billing bounds."""

import asyncio
from copy import copy
from unittest.mock import AsyncMock

import discord
import pytest

from tests.test_conversation_flow import (
    MEMBER,
    conversation,  # noqa: F401 - shared real-SDK/provider-boundary fixture
    member_payload,
    message_payload,
    user_payload,
)
from tests.test_conversation_controls import controls  # noqa: F401


@pytest.mark.asyncio
async def test_retry_replaces_answer_in_visible_and_future_conversation_memory(
    controls,
):
    flow = controls
    flow.answers.extend(["Replacement assistant answer.", "Later answer."])
    history = list(flow.thread.history)
    full_history = list(flow.cog.full_conversation_history[flow.thread_id])
    count = flow.thread.count

    await flow.retry().callback(flow.click())

    assert flow.thread.count == count
    assert len(flow.thread.history) == len(history)
    assert all(
        actual is old for actual, old in zip(flow.thread.history[:-1], history[:-1])
    )
    assert "Replacement assistant answer." in flow.thread.history[-1].text
    assert (
        flow.messages[flow.redo.response.id]["content"]
        == "Replacement assistant answer."
    )
    assert flow.cog.full_conversation_history[flow.thread_id] == [
        *full_history[:-1],
        "Replacement assistant answer.",
    ]
    retry_input = flow.provider_calls[1]["json"]["messages"]
    assert retry_input[-1]["role"] == "user"
    assert "The original completed answer." not in str(retry_input)

    await flow.deliver("My followup question.", channel=flow.thread_id)
    next_input = flow.provider_calls[2]["json"]["messages"]
    assert "Replacement assistant answer." in str(next_input)
    assert "The original completed answer." not in str(next_input)


@pytest.mark.asyncio
async def test_retry_failure_restores_original_completed_turn(controls, monkeypatch):
    flow = controls
    history = list(flow.thread.history)
    full_history = list(flow.cog.full_conversation_history[flow.thread_id])
    from models.openai_model import OpenAIAPIError

    monkeypatch.setattr(
        flow.model,
        "send_chatgpt_chat_request",
        AsyncMock(side_effect=OpenAIAPIError("provider unavailable", 503)),
    )

    await flow.retry().callback(flow.click())

    assert flow.thread.history == history
    assert all(actual is old for actual, old in zip(flow.thread.history, history))
    assert flow.cog.full_conversation_history[flow.thread_id] == full_history
    assert (
        flow.messages[flow.redo.response.id]["content"]
        == "The original completed answer."
    )
    assert flow.cog.awaiting_responses == flow.cog.awaiting_thread_responses == []


@pytest.mark.asyncio
async def test_cancelled_retry_restores_memory_and_releases_ingress(
    controls, monkeypatch
):
    flow = controls
    history = list(flow.thread.history)
    full_history = list(flow.cog.full_conversation_history[flow.thread_id])
    started = asyncio.Event()

    async def wait_for_cancel(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(flow.model, "send_chatgpt_chat_request", wait_for_cancel)
    task = asyncio.create_task(flow.retry().callback(flow.click()))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert flow.thread.history == history
    assert flow.cog.full_conversation_history[flow.thread_id] == full_history
    assert (
        flow.messages[flow.redo.response.id]["content"]
        == "The original completed answer."
    )
    assert not flow.cog.conversation_recovery._active
    assert not flow.cog.conversation_recovery.lock_for(flow.thread_id).locked()
    assert flow.cog.awaiting_responses == flow.cog.awaiting_thread_responses == []


@pytest.mark.asyncio
async def test_pinecone_retry_requires_correction_as_new_turn(controls):
    flow = controls
    history = list(flow.thread.history)
    full_history = list(flow.cog.full_conversation_history[flow.thread_id])
    flow.cog.pinecone_service = object()
    interaction = flow.click()

    await flow.retry().callback(interaction)

    assert len(flow.provider_calls) == 1
    assert flow.thread.history == history
    assert flow.cog.full_conversation_history[flow.thread_id] == full_history
    assert (
        "correction as a new message"
        in flow.adapter.initial[interaction.id]["data"]["content"]
    )


async def other_maintainer_turn(flow, channel, identifier, content):
    data = message_payload(identifier, channel, content)
    data["author"] = user_payload(MEMBER + 1)
    data["member"] = member_payload(MEMBER + 1)
    message = discord.Message(
        state=flow.state, channel=flow.guild.get_thread(channel), data=data
    )
    message._raw_data = data
    await flow.cog.on_message(message)


@pytest.mark.asyncio
async def test_earlier_participant_edit_cannot_replace_later_maintainer_turn(
    conversation,
):
    flow = conversation
    channel = await flow.start()
    flow.answers.extend(["First answer", "Second participant answer"])
    earlier = await flow.deliver("First participant question", channel=channel)
    await other_maintainer_turn(
        flow, channel, earlier + 20, "Second participant question"
    )
    thread = flow.cog.conversation_threads[channel]
    history = list(thread.history)
    full_history = list(flow.cog.full_conversation_history[channel])
    edits = len(flow.edits)

    await flow.deliver(
        "Changed earlier question", channel=channel, identifier=earlier, update=True
    )

    assert len(flow.provider_calls) == 2
    assert thread.history == history
    assert flow.cog.full_conversation_history[channel] == full_history
    assert len(flow.edits) == edits


@pytest.mark.asyncio
async def test_latest_edit_replaces_one_turn_and_future_context(conversation):
    flow = conversation
    channel = await flow.start()
    flow.answers.extend(["Old answer", "Replacement answer", "Followup answer"])
    prompt = await flow.deliver("Original question", channel=channel)
    thread = flow.cog.conversation_threads[channel]
    response = flow.cog.redo_users[MEMBER].response
    before_count = thread.count

    await flow.deliver(
        "Replacement question", channel=channel, identifier=prompt, update=True
    )

    assert thread.count == before_count
    assert len(thread.history) == 3
    assert len(flow.cog.full_conversation_history[channel]) == 2
    assert "Replacement question" in thread.history[-2].text
    assert "Replacement answer" in thread.history[-1].text
    assert flow.messages[response.id]["content"] == "Replacement answer"
    replacement_input = flow.provider_calls[1]["json"]["messages"]
    assert replacement_input[-1]["role"] == "user"
    assert "Old answer" not in str(replacement_input)

    await flow.deliver("Followup", channel=channel)
    followup_input = flow.provider_calls[2]["json"]["messages"]
    assert "Replacement answer" in str(followup_input)
    assert "Old answer" not in str(followup_input)
    assert "Original question" not in str(followup_input)


@pytest.mark.asyncio
async def test_failed_latest_edit_preserves_answer_and_memory(
    conversation, monkeypatch
):
    flow = conversation
    channel = await flow.start()
    flow.answers.append("Original successful answer")
    prompt = await flow.deliver("Original question", channel=channel)
    thread = flow.cog.conversation_threads[channel]
    response = flow.cog.redo_users[MEMBER].response
    history = list(thread.history)
    full_history = list(flow.cog.full_conversation_history[channel])
    from models.openai_model import OpenAIAPIError

    monkeypatch.setattr(
        flow.model,
        "send_chatgpt_chat_request",
        AsyncMock(side_effect=OpenAIAPIError("provider unavailable", 503)),
    )
    await flow.deliver(
        "Replacement question", channel=channel, identifier=prompt, update=True
    )

    assert thread.history == history
    assert all(actual is old for actual, old in zip(thread.history, history))
    assert flow.cog.full_conversation_history[channel] == full_history
    assert flow.messages[response.id]["content"] == "Original successful answer"
    assert flow.cog.awaiting_responses == flow.cog.awaiting_thread_responses == []


@pytest.mark.asyncio
async def test_cancelled_edit_restores_original_turn(conversation, monkeypatch):
    flow = conversation
    channel = await flow.start()
    flow.answers.append("Original successful answer")
    prompt = await flow.deliver("Original question", channel=channel)
    redo = flow.cog.redo_users[MEMBER]
    thread = flow.cog.conversation_threads[channel]
    history = list(thread.history)
    full_history = list(flow.cog.full_conversation_history[channel])
    started = asyncio.Event()

    async def wait_for_cancel(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(flow.model, "send_chatgpt_chat_request", wait_for_cancel)
    after = copy(redo.ctx)
    after.content = "Edited question"
    from services.text_service import TextService

    task = asyncio.create_task(
        TextService.process_conversation_edit(flow.cog, after, {MEMBER: prompt})
    )
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert thread.history == history
    assert flow.cog.full_conversation_history[channel] == full_history
    assert flow.cog.awaiting_responses == flow.cog.awaiting_thread_responses == []


@pytest.mark.asyncio
async def test_pinecone_edit_fails_closed_without_mutating_memory(conversation):
    flow = conversation
    channel = await flow.start()
    flow.answers.append("Original successful answer")
    prompt = await flow.deliver("Original question", channel=channel)
    thread = flow.cog.conversation_threads[channel]
    history = list(thread.history)
    full_history = list(flow.cog.full_conversation_history[channel])
    flow.cog.pinecone_service = object()

    await flow.deliver(
        "Replacement question", channel=channel, identifier=prompt, update=True
    )

    assert len(flow.provider_calls) == 1
    assert thread.history == history
    assert flow.cog.full_conversation_history[channel] == full_history
    assert any(
        "correction as a new message" in message["content"] for message in flow.sent
    )
