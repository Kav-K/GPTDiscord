"""Readiness reconciles configured guilds without touching global commands."""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
from pycord.multicog import Bot


class StopAfterSnapshot(Exception):
    pass


@pytest.fixture
def module(monkeypatch, tmp_path):
    monkeypatch.setenv("ALLOWED_GUILDS", "11,22")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SHARE_DIR", str(tmp_path))
    monkeypatch.setenv("USER_INPUT_API_KEYS", "false")
    result = importlib.import_module("cogs.text_service_cog")
    monkeypatch.setattr(result, "ALLOWED_GUILDS", [11, 22, 11])
    monkeypatch.setattr(result.asyncio, "sleep", AsyncMock())
    return result


def fake_cog(module, bot):
    return SimpleNamespace(
        bot=bot,
        DEBUG_GUILD=0,
        DEBUG_CHANNEL=0,
        instructions={},
        model=SimpleNamespace(model=module.Models.DEFAULT),
        pickle_queue=SimpleNamespace(put=AsyncMock(side_effect=StopAfterSnapshot)),
    )


@pytest.mark.asyncio
async def test_ready_registers_real_commands_only_in_configured_guilds(
    module, monkeypatch
):
    from cogs.commands import Commands
    from cogs.web_search_cog import WebSearchCog

    bot = Bot(intents=discord.Intents.none(), auto_sync_commands=False)
    bot._connection.user = SimpleNamespace(id=99)
    bot.add_cog(Commands(bot, *([None] * 10), transcribe_cog=object()))
    bot.add_cog(WebSearchCog(bot))

    @bot.slash_command(name="global_origin", description="Initially global")
    async def global_origin(ctx):
        pass

    @bot.slash_command(
        name="other_origin",
        description="Initially outside configured guilds",
        guild_ids=[33],
    )
    async def other_origin(ctx):
        pass

    assert global_origin.guild_ids is None
    pending = list(bot.pending_application_commands)
    expected = {(command.name, command.type) for command in pending}
    unavailable = {
        ("index", 1), ("internet", 1), ("code", 1),
        ("translate", 1), ("languages", 1), ("Translate", 3), ("setup", 1),
    }
    assert unavailable.isdisjoint(expected)
    assert {("gpt", 1), ("dalle", 1), ("transcribe", 1), ("web", 1)} <= expected
    remote_global = [{"id": "900", "name": "existing_global", "type": 1}]
    remote_other = [{"id": "901", "name": "existing_other", "type": 1}]
    remote = {
        guild_id: [
            {"id": str(guild_id * 1000 + number), "guild_id": str(guild_id), "name": name, "type": kind}
            for number, (name, kind) in enumerate(sorted(unavailable))
        ]
        for guild_id in (11, 22)
    }
    remote[33] = remote_other
    requests = []

    async def get_guild(application_id, guild_id):
        requests.append(("fetch", guild_id))
        assert application_id == 99
        assert guild_id in (11, 22)
        return remote[guild_id]

    async def upsert_guild(application_id, guild_id, data):
        requests.append(("write", guild_id))
        assert application_id == 99
        assert guild_id in (11, 22)
        assert {
            (command["name"], command.get("type", 1)) for command in data
        } == expected
        remote[guild_id] = [
            dict(
                command,
                type=command.get("type", 1),
                id=str(guild_id * 1000 + number),
                guild_id=str(guild_id),
            )
            for number, command in enumerate(data)
        ]
        return remote[guild_id]

    monkeypatch.setattr(bot.http, "get_guild_commands", get_guild)
    monkeypatch.setattr(bot.http, "bulk_upsert_guild_commands", upsert_guild)
    forbidden = AsyncMock(
        side_effect=AssertionError("Global commands must remain untouched")
    )
    for name in (
        "get_global_commands",
        "bulk_upsert_global_commands",
        "upsert_global_command",
        "edit_global_command",
        "delete_global_command",
        "delete_guild_command",
    ):
        monkeypatch.setattr(bot.http, name, forbidden)
    monkeypatch.setattr(bot, "sync_commands", forbidden)
    cog = fake_cog(module, bot)

    try:
        with pytest.raises(StopAfterSnapshot):
            await module.GPT3ComCon.on_ready(cog)

        assert requests == [("fetch", 11), ("write", 11), ("fetch", 22), ("write", 22)]
        assert all(command.guild_ids == [11, 22] for command in pending)
        assert {int(command["guild_id"]) for command in remote[11] + remote[22]} == {
            11,
            22,
        }
        for guild_id in (11, 22):
            names = {(command["name"], command["type"]) for command in remote[guild_id]}
            assert names == expected
            assert unavailable.isdisjoint(names)
        for command in remote[11] + remote[22]:
            cached = bot.all_commands[command["id"]]
            assert (cached.name, cached.type) == (command["name"], command["type"])
        assert remote[33] is remote_other
        assert remote_global == [{"id": "900", "name": "existing_global", "type": 1}]
        forbidden.assert_not_awaited()
        cog.pickle_queue.put.assert_awaited_once()
        snapshot = cog.pickle_queue.put.call_args.args[0]
        assert snapshot.conversation_threads is cog.conversation_threads
        module.asyncio.sleep.assert_awaited_once_with(15)

        # A reconnect remains guild-scoped and refreshes the ID cache. Let
        # Pycord decide whether its schema comparison requires another write.
        requests.clear()
        cog.pickle_queue.put.reset_mock()
        with pytest.raises(StopAfterSnapshot):
            await module.GPT3ComCon.on_ready(cog)
        assert [request for request in requests if request[0] == "fetch"] == [
            ("fetch", 11),
            ("fetch", 22),
        ]
        assert all(guild_id in (11, 22) for _, guild_id in requests)
        for command in remote[11] + remote[22]:
            assert command["id"] in bot.all_commands
        forbidden.assert_not_awaited()
    finally:
        await bot.close()


@pytest.mark.asyncio
async def test_ready_never_clears_remote_commands_when_no_local_commands_exist(module):
    bot = SimpleNamespace(
        get_guild=lambda _: None,
        pending_application_commands=[],
        register_commands=AsyncMock(),
        sync_commands=AsyncMock(),
    )
    cog = fake_cog(module, bot)

    with pytest.raises(StopAfterSnapshot):
        await module.GPT3ComCon.on_ready(cog)

    bot.register_commands.assert_not_awaited()
    bot.sync_commands.assert_not_awaited()
    cog.pickle_queue.put.assert_awaited_once()
