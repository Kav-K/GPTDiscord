"""Moderation configuration, worker lifetime, and Discord control contracts."""

import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
async def moderation(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SHARE_DIR", str(tmp_path))
    module = importlib.import_module("cogs.moderations_service_cog")

    class Database(dict):
        def commit(self):
            pass

    monkeypatch.setattr(module, "MOD_DB", Database())
    for name in ("moderation_tasks", "moderation_queues"):
        monkeypatch.setattr(module.Moderation, name, {})
    monkeypatch.setattr(module.Moderation, "moderations_launched", [])
    channel = SimpleNamespace(id=12, guild=SimpleNamespace(id=11))
    cog = module.ModerationsService(
        SimpleNamespace(fetch_channel=AsyncMock(return_value=channel), guilds=[]),
        None,
        SimpleNamespace(),
    )
    ctx = SimpleNamespace(
        guild_id=11,
        guild=SimpleNamespace(channels=[]),
        defer=AsyncMock(),
        respond=AsyncMock(),
    )
    yield SimpleNamespace(module=module, cog=cog, ctx=ctx, channel=channel)
    for task in module.Moderation.moderation_tasks.values():
        task.cancel()
    await asyncio.gather(
        *module.Moderation.moderation_tasks.values(), return_exceptions=True
    )


async def test_zero_threshold_is_saved_without_enabling_moderation(moderation):
    f = moderation
    await f.cog.config_command(f.ctx, "warn", 0.0, None, None, None, None, None, None)
    assert f.cog.get_or_set_warn_set(11)["hate"] == 0.0
    assert not f.cog.check_guild_moderated(11)
    assert not f.module.Moderation.moderation_tasks
    assert "saved" in f.ctx.respond.call_args.args[0]


@pytest.mark.parametrize("value", [-0.1, 1.1, float("nan")])
async def test_invalid_threshold_keeps_settings(moderation, value):
    f = moderation
    previous = f.cog.get_or_set_warn_set(11)
    await f.cog.config_command(f.ctx, "warn", value, None, None, None, None, None, None)
    assert f.cog.get_or_set_warn_set(11) == previous
    assert "between 0 and 1" in f.ctx.respond.call_args.args[0]


async def test_reconnect_and_repeated_off_do_not_duplicate_or_leak_workers(moderation):
    f = moderation
    f.cog.set_moderated_alert_channel(11, 12)
    await f.cog.check_and_launch_moderations(11)
    first = f.module.Moderation.moderation_tasks[11]
    await f.cog.check_and_launch_moderations(11)
    assert f.module.Moderation.moderation_tasks[11] is first
    assert f.module.Moderation.moderations_launched == [11]
    await f.cog.stop_moderations_service(11)
    await f.cog.stop_moderations_service(11)
    assert first.cancelled()
    assert not f.module.Moderation.moderation_tasks
    assert not f.module.Moderation.moderation_queues
    assert not f.cog.check_guild_moderated(11)


async def test_unavailable_alert_channel_does_not_block_later_guilds(moderation):
    f = moderation
    f.cog.bot.guilds = [SimpleNamespace(id=10), SimpleNamespace(id=11)]
    f.cog.check_and_launch_moderations = AsyncMock(side_effect=[ValueError(), None])
    await f.cog.on_ready()
    assert [
        call.args[0] for call in f.cog.check_and_launch_moderations.call_args_list
    ] == [10, 11]


async def test_overlapping_ready_events_share_one_worker(moderation):
    f = moderation
    f.cog.set_moderated_alert_channel(11, 12)
    both_started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def fetch(channel_id):
        nonlocal calls
        calls += 1
        if calls == 2:
            both_started.set()
        await release.wait()
        return f.channel

    f.cog.bot.fetch_channel.side_effect = fetch
    tasks = [
        asyncio.create_task(f.cog.check_and_launch_moderations(11)) for _ in range(2)
    ]
    try:
        await asyncio.wait_for(both_started.wait(), 1)
    finally:
        release.set()
        await asyncio.gather(*tasks)
    assert len(f.module.Moderation.moderation_tasks) == 1
    assert f.module.Moderation.moderations_launched == [11]


async def test_missing_or_foreign_alert_channel_does_not_enable(moderation):
    f = moderation
    await f.cog.moderations_command(f.ctx, "on", "missing-name")
    assert not f.cog.check_guild_moderated(11)
    f.channel.guild.id = 99
    await f.cog.moderations_command(f.ctx, "on", "12")
    assert not f.cog.check_guild_moderated(11)
    assert not f.module.Moderation.moderation_tasks


async def test_invalid_default_channel_rolls_back_and_can_be_retried(
    moderation, monkeypatch
):
    f = moderation
    monkeypatch.setattr(f.module.Moderation, "moderation_alerts_channel", "12")
    f.channel.guild.id = 99
    await f.cog.moderations_command(f.ctx, "on", None)
    assert not f.cog.check_guild_moderated(11)
    assert not f.module.Moderation.moderation_tasks
    f.channel.guild.id = 11
    await f.cog.moderations_command(f.ctx, "on", None)
    assert f.cog.check_guild_moderated(11)
    assert f.cog.get_moderated_alert_channel(11) == 12


@pytest.mark.parametrize("decision", ["none", "warn", "delete"])
async def test_queue_applies_only_requested_moderation_action(
    moderation, monkeypatch, decision
):
    f = moderation
    service = importlib.import_module("services.moderations_service")
    scores = dict.fromkeys(f.cog.default_warn_set.keys, 0.0)
    scores["hate"] = {"none": 0.0, "warn": 0.2, "delete": 0.9}[decision]
    provider = AsyncMock(
        return_value={
            "results": [{"flagged": decision != "none", "category_scores": scores}]
        }
    )
    monkeypatch.setattr(service.model, "send_moderations_request", provider)
    message = SimpleNamespace(
        id=13,
        guild=SimpleNamespace(id=11, name="Test"),
        channel=SimpleNamespace(id=12),
        author=SimpleNamespace(mention="<@42>"),
        content="benign controlled fixture",
        reply=AsyncMock(),
        delete=AsyncMock(),
        add_reaction=AsyncMock(),
    )
    reply = SimpleNamespace(
        guild=message.guild, channel=message.channel, id=14, edit=AsyncMock()
    )
    message.reply.return_value = reply
    alerts = SimpleNamespace(send=AsyncMock(return_value=reply))
    queue = asyncio.Queue()
    await queue.put(service.Moderation(message, 0))
    task = asyncio.create_task(
        service.Moderation.process_moderation_queue(
            queue,
            0,
            0,
            alerts,
            service.ThresholdSet(*([0.1] * 7)),
            service.ThresholdSet(*([0.8] * 7)),
        )
    )
    try:
        for _ in range(10):
            await asyncio.sleep(0)
            if provider.await_count:
                break
        provider.assert_awaited_once_with(message.content)
        assert message.delete.await_count == (decision == "delete")
        assert message.add_reaction.await_count == (decision == "warn")
        assert alerts.send.await_count == (decision != "none")
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_approve_acknowledges_before_discord_work(moderation):
    from services.moderations_service import ApproveMessageButton

    response = SimpleNamespace(defer=AsyncMock())

    async def clear():
        response.defer.assert_awaited_once_with(ephemeral=True)

    message = SimpleNamespace(clear_reactions=AsyncMock(side_effect=clear))
    alert = SimpleNamespace(delete=AsyncMock())
    button = ApproveMessageButton(message, (alert,), 0)
    await button.callback(SimpleNamespace(response=response))
    alert.delete.assert_awaited_once()
