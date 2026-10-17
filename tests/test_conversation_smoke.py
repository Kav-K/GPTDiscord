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
        rest_recovery=False,
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


@pytest.mark.asyncio
async def test_explicit_recovery_requires_confirmed_missing_gateway_then_runs(
    smoke, tmp_path, monkeypatch
):
    monkeypatch.setenv("CONVERSATION_RECOVERY_GUILD_IDS", str(GUILD))
    smoke.args.rest_recovery = True
    smoke.scenario["guild_cached"] = False
    report = {}

    await smoke.module.run_checks(smoke.args, report, tmp_path)

    assert report["gateway_tested"] is False
    assert report["gateway"]["failure"]["category"] == "gateway_target_guild_missing"
    assert report["recovery"]["gateway_target_missing_confirmed"] is True
    assert report["recovery"]["human_authorship_simulated"] is True
    assert report["recovery"]["edit_delivery_tested"] is False
    # Only run() can mark the recovery transport tested after actual poll calls.
    assert report["recovery_transport_tested"] is False
    assert smoke.events == [("close",), ("hybrid",)]
    smoke.hybrid.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "configured,category",
    [
        (None, "recovery_guilds_required"),
        ("", "recovery_guilds_required"),
        ("not-a-guild", "recovery_guilds_invalid"),
        (f"{GUILD},", "recovery_guilds_invalid"),
        (str(GUILD + 1), "recovery_target_not_configured"),
    ],
)
async def test_recovery_configuration_rejected_before_any_client_or_provider(
    smoke, tmp_path, monkeypatch, configured, category
):
    smoke.args.rest_recovery = True
    if configured is None:
        monkeypatch.delenv("CONVERSATION_RECOVERY_GUILD_IDS", raising=False)
    else:
        monkeypatch.setenv("CONVERSATION_RECOVERY_GUILD_IDS", configured)

    with pytest.raises(smoke.module.SmokeFailure, match=category):
        await smoke.module.run_checks(smoke.args, {}, tmp_path)

    assert not smoke.clients
    smoke.hybrid.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_mode_does_not_fake_missing_guild_when_gateway_works(
    smoke, tmp_path, monkeypatch
):
    monkeypatch.setenv("CONVERSATION_RECOVERY_GUILD_IDS", str(GUILD))
    smoke.args.rest_recovery = True
    report = {}

    await smoke.module.run_checks(smoke.args, report, tmp_path)

    assert report["status"] == "failed"
    assert report["failure"]["category"] == "recovery_missing_gateway_not_confirmed"
    assert report["gateway"]["target_guild_cached"] is True
    assert report["recovery_transport_tested"] is False
    assert report["provider"]["attempts"] == []
    smoke.hybrid.assert_not_awaited()


def test_recovery_mode_still_requires_live_opt_in_and_excludes_rest_only(smoke, capsys):
    with pytest.raises(SystemExit) as error:
        smoke.module.main(["--rest-recovery"])
    assert error.value.code == 2
    assert "--live is required" in capsys.readouterr().err
    with pytest.raises(SystemExit) as error:
        smoke.module.main(["--live", "--rest-recovery", "--rest-only"])
    assert error.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err
    assert not smoke.clients
    smoke.hybrid.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["timeout", "gateway_closed_before_ready", "client_or_response"]
)
async def test_gateway_failures_other_than_observed_missing_guild_cannot_enable_recovery(
    smoke, tmp_path, monkeypatch, failure
):
    monkeypatch.setenv("CONVERSATION_RECOVERY_GUILD_IDS", str(GUILD))
    smoke.args.rest_recovery = True

    async def failed_preflight(args, report):
        report["gateway"] = {
            "ready": True,
            "target_guild_cached": False,
            "client_closed": True,
            "ready_payloads": [{"target_guild_listed": False}],
            "failure": {"category": failure},
        }
        return False

    monkeypatch.setattr(smoke.module, "gateway_preflight", failed_preflight)
    report = {}
    await smoke.module.run_checks(smoke.args, report, tmp_path)

    assert report["status"] == "failed"
    assert report["provider"]["attempts"] == []
    assert report["recovery_transport_tested"] is False
    smoke.hybrid.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_fixture_adapter_reads_real_payload_and_changes_only_registered_own_message(
    smoke,
):
    raw = {
        "id": str(FIXTURE),
        "channel_id": str(CHANNEL),
        "author": {"id": str(BOT), "bot": True},
        "content": "Bot-authored fixture",
        "type": 19,
        "message_reference": {"message_id": str(FIXTURE - 1)},
    }
    unrelated = {**raw, "id": str(FIXTURE + 1)}
    original = AsyncMock(return_value=[raw, unrelated])
    member = {"user": {"id": str(MEMBER), "bot": False}, "roles": [str(ROLE)]}
    membership = AsyncMock(return_value=member)
    reader = smoke.module.RecoveryFixtureReader(
        original,
        thread_id=CHANNEL,
        guild_id=GUILD,
        bot_id=BOT,
        member_id=MEMBER,
        fixtures={FIXTURE: {"content": "Synthetic human prompt"}},
        membership=membership,
    )

    result = await reader(CHANNEL, 20, after=FIXTURE - 1)

    original.assert_awaited_once_with(CHANNEL, 20, after=FIXTURE - 1)
    membership.assert_awaited_once()
    assert result[0]["author"] == member["user"]
    assert result[0]["member"] == member
    assert result[0]["content"] == "Synthetic human prompt"
    assert result[0]["type"] == 19
    assert result[0]["message_reference"] == raw["message_reference"]
    assert result[1] is unrelated
    assert raw["author"]["bot"] is True
    assert raw["content"] == "Bot-authored fixture"


@pytest.mark.asyncio
async def test_recovery_fixture_adapter_refuses_out_of_scope_reads_and_human_owned_sources(
    smoke,
):
    original = AsyncMock(
        return_value=[
            {
                "id": str(FIXTURE),
                "channel_id": str(CHANNEL),
                "author": {"id": str(MEMBER), "bot": False},
                "content": "Real human message",
            }
        ]
    )
    membership = AsyncMock()
    reader = smoke.module.RecoveryFixtureReader(
        original,
        thread_id=CHANNEL,
        guild_id=GUILD,
        bot_id=BOT,
        member_id=MEMBER,
        fixtures={FIXTURE: {"content": "Synthetic human prompt"}},
        membership=membership,
    )
    for channel, limit in [(CHANNEL + 1, 20), (CHANNEL, 21), (CHANNEL, 0)]:
        with pytest.raises(smoke.module.SmokeFailure, match="recovery_poll_scope"):
            await reader(channel, limit)
    original.assert_not_awaited()
    with pytest.raises(
        smoke.module.SmokeFailure, match="recovery_fixture_not_bot_owned"
    ):
        await reader(CHANNEL, 20)
    membership.assert_not_awaited()
