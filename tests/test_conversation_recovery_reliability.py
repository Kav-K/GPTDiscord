"""Recovery scheduling and malformed-record regressions; no external requests."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from tests.test_conversation_flow import GUILD, MEMBER, message_payload
from tests.test_conversation_recovery import (
    FIRST,
    THREAD,
    recovery as recovery,
    thread_payload,
)

pytestmark = pytest.mark.asyncio


async def prepare_threads(flow, monkeypatch, count=1):
    monkeypatch.setattr(flow.service, "start", lambda: None)
    channels, history = {}, {}
    for index in range(count):
        identifier = THREAD + index * 1000
        data = thread_payload(identifier)
        thread = discord.Thread(guild=flow.guild, state=flow.state, data=deepcopy(data))
        flow.guild._threads[identifier] = thread
        flow.cog.conversation_threads[identifier] = SimpleNamespace(history=[])
        if identifier not in flow.cog.conversation_thread_owners[MEMBER]:
            flow.cog.conversation_thread_owners[MEMBER].append(identifier)
        channels[identifier] = data
        history[identifier] = [
            message_payload(identifier + 10, identifier, "A question")
        ]
        await flow.service.watch_thread(thread, new=True)
    flow.state.http.get_channel.side_effect = lambda identifier: deepcopy(
        channels[identifier]
    )

    async def logs(identifier, limit, **kwargs):
        return deepcopy(history[identifier])

    monkeypatch.setattr(flow.state.http, "logs_from", AsyncMock(side_effect=logs))
    return history


@pytest.mark.parametrize(
    "malformed", ["null-author", "list-author", "bad-id", "missing-id", "not-object"]
)
async def test_malformed_record_does_not_poison_later_authorized_turn(
    recovery, monkeypatch, malformed
):
    flow = recovery
    history = await prepare_threads(flow, monkeypatch)
    poison = message_payload(FIRST, THREAD, "An unsupported record")
    if malformed == "null-author":
        poison["author"] = None
    elif malformed == "list-author":
        poison["author"] = []
    elif malformed == "bad-id":
        poison["id"] = "not-a-message-id"
    elif malformed == "missing-id":
        poison.pop("id")
    else:
        poison = None
    history[THREAD] = [poison, message_payload(FIRST + 1, THREAD, "A valid later turn")]

    await flow.service.poll_once()
    await flow.service.poll_once()

    assert flow.processed == [("create", FIRST + 1, "A valid later turn")]
    assert flow.service._cursor(THREAD) == FIRST + 1
    assert THREAD not in flow.service._retry_after


async def test_blocked_thread_does_not_delay_another_thread(recovery, monkeypatch):
    flow = recovery
    await prepare_threads(flow, monkeypatch, count=2)
    started, other_finished, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def generate(message, **kwargs):
        if message.channel.id == THREAD:
            started.set()
            await release.wait()
        else:
            other_finished.set()

    flow.cog._process_message.side_effect = generate
    polling = asyncio.create_task(flow.service.poll_once())
    try:
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.wait_for(other_finished.wait(), 1)
    finally:
        release.set()
        await asyncio.gather(polling, return_exceptions=True)


async def test_background_ticks_keep_serving_healthy_thread_while_one_turn_is_blocked(
    recovery, monkeypatch
):
    flow = recovery
    history = await prepare_threads(flow, monkeypatch, count=2)
    started, release = asyncio.Event(), asyncio.Event()
    first_healthy, second_healthy = asyncio.Event(), asyncio.Event()
    healthy = []

    async def generate(message, **kwargs):
        if message.channel.id == THREAD:
            started.set()
            await release.wait()
        else:
            healthy.append(message.id)
            (first_healthy if len(healthy) == 1 else second_healthy).set()

    flow.cog._process_message.side_effect = generate
    try:
        await asyncio.wait_for(flow.service.poll_once(wait=False), 1)
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.wait_for(first_healthy.wait(), 1)
        assert healthy == [THREAD + 1010]
        history[THREAD + 1000].append(
            message_payload(THREAD + 1011, THREAD + 1000, "Another turn")
        )
        await asyncio.wait_for(flow.service.poll_once(wait=False), 1)
        await asyncio.wait_for(second_healthy.wait(), 1)
        assert healthy == [THREAD + 1010, THREAD + 1011]
        assert not release.is_set()
    finally:
        release.set()
        await flow.service.close()


async def test_overlapping_polls_preserve_thread_order_and_latest_cursor(
    recovery, monkeypatch
):
    flow = recovery
    history = await prepare_threads(flow, monkeypatch)
    history[THREAD] = [
        message_payload(FIRST, THREAD, "First"),
        message_payload(FIRST + 1, THREAD, "Second"),
    ]
    started, release = asyncio.Event(), asyncio.Event()
    handled = []

    async def generate(message, **kwargs):
        handled.append(message.id)
        if message.id == FIRST:
            started.set()
            await release.wait()

    flow.cog._process_message.side_effect = generate
    first = asyncio.create_task(flow.service.poll_once())
    await asyncio.wait_for(started.wait(), 1)
    history[THREAD] = [message_payload(FIRST + 2, THREAD, "Third")]
    second = asyncio.create_task(flow.service.poll_once())
    try:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert flow.state.http.logs_from.await_count == 1
    finally:
        release.set()
        await asyncio.gather(first, second)
    assert handled == [FIRST, FIRST + 1]
    await flow.service.poll_once()
    assert handled == [FIRST, FIRST + 1, FIRST + 2]
    assert flow.service._cursor(THREAD) == FIRST + 2


async def test_durable_cursor_cannot_move_backwards(recovery):
    flow = recovery
    newest = 2**63 + 100
    flow.service._save_cursor(THREAD, GUILD, newest)
    flow.service._save_cursor(THREAD, GUILD, FIRST)
    assert flow.service._cursor(THREAD) == newest


async def test_scheduler_bounds_jobs_and_concurrency_without_starving_later_threads(
    recovery, monkeypatch
):
    from services.conversation_recovery_service import (
        MAX_CONCURRENT_THREADS,
        MAX_THREADS,
    )

    flow = recovery
    await prepare_threads(flow, monkeypatch, count=MAX_THREADS + 5)
    release, slots_filled = asyncio.Event(), asyncio.Event()
    active = peak = 0
    handled = []

    async def generate(message, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == MAX_CONCURRENT_THREADS:
            slots_filled.set()
        try:
            await release.wait()
            handled.append(message.channel.id)
        finally:
            active -= 1

    flow.cog._process_message.side_effect = generate
    try:
        await flow.service.poll_once(wait=False)
        await asyncio.wait_for(slots_filled.wait(), 1)
        assert len(flow.service._poll_tasks) == MAX_THREADS
        await flow.service.poll_once(wait=False)
        assert len(flow.service._poll_tasks) == MAX_THREADS
        assert peak == MAX_CONCURRENT_THREADS
    finally:
        release.set()
        await asyncio.gather(*flow.service._poll_tasks.values())
    await flow.service.poll_once()
    assert set(handled) == set(flow.cog.conversation_threads)
    assert len(handled) == MAX_THREADS + 5
    assert peak <= MAX_CONCURRENT_THREADS


async def test_close_cancels_queued_jobs_and_drains_already_claimed_turns(
    recovery, monkeypatch
):
    from services.conversation_recovery_service import MAX_CONCURRENT_THREADS

    flow = recovery
    await prepare_threads(flow, monkeypatch, count=MAX_CONCURRENT_THREADS + 2)
    release, slots_filled = asyncio.Event(), asyncio.Event()
    completed, cancelled = [], []
    started = 0

    async def generate(message, **kwargs):
        nonlocal started
        started += 1
        if started == MAX_CONCURRENT_THREADS:
            slots_filled.set()
        try:
            await release.wait()
            completed.append(message.id)
        except asyncio.CancelledError:
            cancelled.append(message.id)
            raise

    flow.cog._process_message.side_effect = generate
    polling = asyncio.create_task(flow.service.poll_once())
    await asyncio.wait_for(slots_filled.wait(), 1)
    closing = asyncio.create_task(flow.service.close())
    try:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert cancelled == []
        assert not closing.done()
    finally:
        release.set()
        await asyncio.gather(polling, closing)
    assert len(completed) == MAX_CONCURRENT_THREADS
    assert cancelled == []
    assert not flow.service._active
    assert not flow.service._poll_tasks


async def test_shutdown_drains_explicit_controls_and_rejects_new_paid_work(recovery):
    service = recovery.service
    started, release = asyncio.Event(), asyncio.Event()

    async def retry_control():
        async with service.active_control():
            started.set()
            await release.wait()

    retry = asyncio.create_task(retry_control())
    await asyncio.wait_for(started.wait(), 1)
    closing = asyncio.create_task(service.close())
    try:
        await asyncio.sleep(0)
        assert not closing.done()
        with pytest.raises(RuntimeError, match="shutting down"):
            async with service.active_control():
                pytest.fail("A closed service must not start another paid request")
    finally:
        release.set()
        await asyncio.gather(retry, closing)
    assert not service._active
