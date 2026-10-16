"""The autonomous smoke must detect a missing gateway before paid REST work."""

import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

GUILD = 1061370086331523176
CHANNEL = 1471696285433204940
ROLE = 1062146522365571123
MEMBER = 1500000000000000003
BOT = 1168110042826940446
FIXTURE = 1500000000000000005


@pytest.fixture
def smoke(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts/live_conversation_smoke.py"
    spec = importlib.util.spec_from_file_location("conversation_smoke_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("DISCORD_TOKEN", "offline-gateway-token")
    monkeypatch.setenv("BOT_ACCESS_ROLE_IDS", str(ROLE))
    monkeypatch.setattr(module, "GATEWAY_ECHO_TIMEOUT", 0.02)
    monkeypatch.setattr(module, "GATEWAY_TIMEOUT", 1)
    events, clients = [], []
    scenario = {"guild_cached": True, "echo": "matching"}

    class Channel:
        id = CHANNEL
        guild = SimpleNamespace(id=GUILD)

        async def send(self, content, **kwargs):
            events.append(("post", CHANNEL, FIXTURE))
            assert "Temporary GPTDiscord gateway diagnostic" in content
            mentions = kwargs["allowed_mentions"]
            assert (
                mentions.everyone is False
                and mentions.users is False
                and mentions.roles is False
            )
            client = clients[-1]

            async def delete():
                events.append(("delete", CHANNEL, FIXTURE))

            posted = SimpleNamespace(
                id=FIXTURE, channel=self, author=client.user, delete=delete
            )
            echo = SimpleNamespace(
                id=FIXTURE,
                channel=self,
                guild=self.guild,
                author=client.user,
            )
            if scenario["echo"] == "wrong-id":
                echo.id += 1
            elif scenario["echo"] == "wrong-author":
                echo.author = SimpleNamespace(id=BOT + 1)
            # Deliberately deliver before the REST POST returns: a wait_for
            # installed after send() would lose this genuine gateway ordering.
            await client.on_message(echo)
            return posted

    channel = Channel()

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["intents"].value == discord.Intents.all().value
            self.user = SimpleNamespace(id=BOT, bot=True)
            self.guilds = [channel.guild] if scenario["guild_cached"] else []
            self.closed = asyncio.Event()
            self.http = SimpleNamespace(
                get_roles=AsyncMock(
                    return_value=[{"id": str(ROLE), "name": "Maintainer"}]
                ),
                get_member=AsyncMock(
                    return_value={
                        "user": {"id": str(MEMBER), "bot": False},
                        "roles": [str(ROLE)],
                    }
                ),
            )
            self.fetch_channel = AsyncMock(return_value=channel)
            clients.append(self)

        def event(self, callback):
            setattr(self, callback.__name__, callback)
            return callback

        def get_guild(self, identifier):
            assert identifier == GUILD
            return channel.guild if self.guilds else None

        async def start(self, token, *, reconnect):
            assert token == "offline-gateway-token" and reconnect is False
            await self.on_socket_response(
                {
                    "t": "READY",
                    "s": 1,
                    "d": {"guilds": [{"id": str(GUILD)}] if self.guilds else []},
                }
            )
            await self.on_ready()
            await self.closed.wait()

        async def close(self):
            events.append(("close",))
            self.closed.set()

    monkeypatch.setattr(discord, "Client", Client)
    monkeypatch.setattr(discord, "TextChannel", Channel)

    async def hybrid(args, report, directory):
        events.append(("hybrid",))
        report["status"] = "passed"

    hybrid = AsyncMock(side_effect=hybrid)
    monkeypatch.setattr(module, "run", hybrid)
    args = SimpleNamespace(
        guild_id=GUILD,
        channel_id=CHANNEL,
        member_id=MEMBER,
        restriction=str(ROLE),
        rest_only=False,
    )
    return SimpleNamespace(
        module=module,
        args=args,
        scenario=scenario,
        events=events,
        clients=clients,
        hybrid=hybrid,
    )


@pytest.mark.asyncio
async def test_missing_gateway_guild_stops_before_post_or_hybrid_provider_work(
    smoke, tmp_path
):
    smoke.scenario["guild_cached"] = False
    report = {}
    await smoke.module.run_checks(smoke.args, report, tmp_path)

    assert report["status"] == "failed"
    assert report["gateway_tested"] is False
    assert report["gateway"]["ready"] is True
    assert report["gateway"]["target_guild_cached"] is False
    assert report["gateway"]["failure"]["category"] == "gateway_target_guild_missing"
    assert report["provider"]["attempts"] == []
    assert smoke.events == [("close",)]
    smoke.hybrid.assert_not_awaited()
    smoke.clients[0].http.get_member.assert_not_awaited()
    smoke.clients[0].fetch_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_matching_own_echo_before_send_returns_cleans_fixture_then_allows_hybrid(
    smoke, tmp_path
):
    report = {}
    await smoke.module.run_checks(smoke.args, report, tmp_path)

    assert report["status"] == "passed" and report["gateway_tested"] is True
    assert report["gateway"]["echo_seen"] is True
    assert report["gateway"]["temporary_message_deleted"] is True
    assert report["gateway"]["client_closed"] is True
    assert smoke.events == [
        ("post", CHANNEL, FIXTURE),
        ("delete", CHANNEL, FIXTURE),
        ("close",),
        ("hybrid",),
    ]
    smoke.hybrid.assert_awaited_once()
    assert "offline-gateway-token" not in json.dumps(report)


@pytest.mark.asyncio
@pytest.mark.parametrize("echo", ["wrong-id", "wrong-author"])
async def test_unrelated_gateway_message_cannot_pass_echo_and_still_cleans_own_fixture(
    smoke, tmp_path, echo
):
    smoke.scenario["echo"] = echo
    report = {}
    await smoke.module.run_checks(smoke.args, report, tmp_path)

    assert report["status"] == "failed" and report["gateway_tested"] is False
    assert report["gateway"]["echo_seen"] is False
    assert (
        report["gateway"]["failure"]["category"] == "gateway_own_message_echo_missing"
    )
    assert report["gateway"]["temporary_message_deleted"] is True
    assert report["provider"]["attempts"] == []
    assert smoke.events == [
        ("post", CHANNEL, FIXTURE),
        ("delete", CHANNEL, FIXTURE),
        ("close",),
    ]
    smoke.hybrid.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_rest_only_mode_labels_gateway_untested(smoke, tmp_path):
    smoke.args.rest_only = True
    report = {}
    await smoke.module.run_checks(smoke.args, report, tmp_path)

    assert report["gateway_tested"] is False
    assert report["gateway"]["status"] == "skipped"
    assert not smoke.clients
    smoke.hybrid.assert_awaited_once()
