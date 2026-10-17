"""The autonomous smoke must detect a missing gateway before paid REST work."""

import asyncio
from copy import deepcopy
import importlib.util
import itertools
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import discord
import pytest

from tests.test_conversation_flow import conversation

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
    actual_run = module.run
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
        actual_run=actual_run,
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


def test_extended_mode_requires_explicit_recovery_and_keeps_default_budget(
    smoke, capsys
):
    assert smoke.module.limits_for(smoke.args)["requests"] == 3
    smoke.args.extended_rest_recovery = True
    with pytest.raises(
        smoke.module.SmokeFailure, match="extended_requires_rest_recovery"
    ):
        smoke.module.limits_for(smoke.args)
    smoke.args.rest_recovery = True
    limits = smoke.module.limits_for(smoke.args)
    assert limits["requests"] == 6 and limits["input_bytes"] == 60_000
    with pytest.raises(SystemExit) as error:
        smoke.module.main(["--live", "--extended-rest-recovery"])
    assert error.value.code == 2
    assert "requires --rest-recovery" in capsys.readouterr().err
    assert not smoke.clients


@pytest.mark.parametrize("extended,cap", [(False, 3), (True, 6)])
async def test_provider_cap_counts_real_attempts_and_cannot_exceed_output_budget(
    smoke, monkeypatch, extended, cap
):
    import sys

    sent = []

    async def original(self, endpoint, payload, key):
        sent.append(deepcopy(payload))
        return {"usage": {"total_tokens": 5}}

    def wrapped():
        pass

    wrapped.__wrapped__ = original
    fake_model = type("OfflineModel", (), {"_post_json": wrapped})
    monkeypatch.setitem(
        sys.modules, "models.openai_model", SimpleNamespace(Model=fake_model)
    )
    smoke.args.rest_recovery = extended
    smoke.args.extended_rest_recovery = extended
    limits = smoke.module.limits_for(smoke.args)
    provider = smoke.module.BoundedProvider(
        fake_model(),
        "gpt-6.1-sol",
        max_requests=limits["requests"],
        max_input_bytes=limits["input_bytes"],
    )
    payload = {
        "model": "gpt-6.1-sol",
        "messages": [{"role": "user", "content": "offline fixture"}],
        "max_completion_tokens": 900,
    }
    for _ in range(cap):
        await provider("chat/completions", payload)
    with pytest.raises(
        smoke.module.SmokeFailure, match="provider_budget_or_model_guard"
    ):
        await provider("chat/completions", payload)
    assert len(sent) == cap and provider.blocked == 1
    assert all(item["max_completion_tokens"] == 512 for item in sent)
    assert all(item["message_roles"] == ["user"] for item in provider.calls)
    assert "offline fixture" not in json.dumps(provider.calls)


async def test_multiple_owned_resources_cleanup_continues_and_never_deletes_foreign_thread(
    smoke,
):
    threads = {
        FIXTURE: {
            "id": str(FIXTURE),
            "guild_id": str(GUILD),
            "parent_id": str(CHANNEL),
            "owner_id": str(BOT),
            "type": 11,
        },
        FIXTURE
        + 1: {
            "id": str(FIXTURE + 1),
            "guild_id": str(GUILD),
            "parent_id": str(CHANNEL),
            "owner_id": str(BOT),
            "type": 12,
        },
    }
    deleted = []
    bot = SimpleNamespace(
        user=SimpleNamespace(id=BOT),
        http=SimpleNamespace(
            get_channel=AsyncMock(side_effect=lambda identifier: threads[identifier]),
            delete_channel=AsyncMock(
                side_effect=lambda identifier, **kwargs: deleted.append(identifier)
            ),
            get_message=AsyncMock(return_value={"author": {"id": str(BOT)}}),
            delete_message=AsyncMock(),
        ),
    )
    owned = smoke.module.OwnedDiscordResources(bot, GUILD, CHANNEL)
    owned.thread(threads[FIXTURE])
    owned.thread(threads[FIXTURE + 1], "owned_private_thread")
    owned.starter(FIXTURE + 2)
    threads[FIXTURE]["owner_id"] = str(MEMBER)
    results = await owned.cleanup()
    assert [item["status"] for item in results] == ["failed", "deleted", "deleted"]
    assert deleted == [FIXTURE + 1]
    bot.http.delete_message.assert_awaited_once_with(CHANNEL, FIXTURE + 2)


async def test_callback_context_uses_real_sdk_without_gateway_cache_and_local_ack(
    conversation, smoke
):
    flow = conversation
    channel = await flow.start()
    thread = flow.guild.get_thread(channel)
    response = discord.Message(
        state=flow.state, channel=thread, data=flow.messages[channel]
    )
    flow.state._guilds.clear()
    from tests.test_conversation_flow import member_payload

    interaction, acknowledgement = smoke.module.control_interaction(
        flow.bot, thread, response, member_payload(), "conversation_end"
    )
    assert isinstance(interaction, discord.Interaction)
    assert interaction.channel is thread and interaction.guild is thread.guild
    assert flow.bot.get_guild(GUILD) is None
    assert not interaction.response.is_done()
    await interaction.response.defer(ephemeral=True)
    await interaction.followup.send("Synthetic acknowledgement", ephemeral=True)
    assert acknowledgement.is_done() and acknowledgement.count == 2
    assert acknowledgement.private_only is True


@pytest.mark.parametrize("mode", ["extended-recovery", "recovery", "hybrid"])
async def test_live_modes_run_real_handlers_with_exact_budgets_and_owned_cleanup(
    conversation, smoke, monkeypatch, tmp_path, mode
):
    from tests.test_conversation_flow import BOT_ROLE, member_payload, message_payload
    from tests.test_conversation_recovery import guild_payload, thread_payload

    flow = conversation
    extended = mode == "extended-recovery"
    recovery_mode = mode != "hybrid"
    bot = flow.bot
    monkeypatch.setattr(discord, "TextChannel", type(flow.ctx.channel))
    bot.remove_cog("GPT3ComCon")
    flow.state._guilds.clear()
    if recovery_mode:
        monkeypatch.setenv("CONVERSATION_RECOVERY_GUILD_IDS", str(GUILD))
    monkeypatch.setattr(discord, "Bot", lambda **kwargs: bot)
    monkeypatch.setattr(bot, "login", AsyncMock())
    sequence = itertools.count(1600000000000000000)
    payload = guild_payload()
    for role in payload["roles"]:
        permissions = flow.guild.get_role(int(role["id"])).permissions
        if int(role["id"]) == BOT_ROLE:
            permissions.create_private_threads = True
        role["permissions"] = str(permissions.value)
    channels = {CHANNEL: deepcopy(payload["channels"][0])}
    members, deleted_threads = set(), []

    async def send(channel, content=None, **kwargs):
        identifier = next(sequence)
        data = message_payload(identifier, channel, content or "", bot=True)
        data["embeds"] = kwargs.get("embeds") or []
        data["components"] = kwargs.get("components") or []
        if kwargs.get("message_reference"):
            data["message_reference"] = kwargs["message_reference"]
            data["type"] = 19
        flow.messages[identifier] = deepcopy(data)
        flow.sent.append(deepcopy(data))
        return data

    async def start(channel, identifier=None, **kwargs):
        identifier = next(sequence) if identifier is None else identifier
        data = thread_payload(identifier)
        data["type"] = kwargs.get("type", 11)
        channels[identifier] = deepcopy(data)
        return data

    async def logs(channel, limit, **kwargs):
        values = [
            deepcopy(item)
            for item in flow.messages.values()
            if int(item["channel_id"]) == channel
            and int(item["id"]) > kwargs.get("after", 0)
        ]
        return sorted(values, key=lambda item: int(item["id"]), reverse=True)[:limit]

    async def edit_channel(channel, **kwargs):
        data = channels[channel]
        for key, value in kwargs.items():
            if key in {"archived", "locked"}:
                data["thread_metadata"][key] = value
            else:
                data[key] = value
        return deepcopy(data)

    async def thread_member(route, **kwargs):
        parts = urlsplit(route.url).path.split("/")
        assert route.method == "GET" and parts[-2] == "thread-members"
        identifier, member = int(parts[-3]), int(parts[-1])
        assert (identifier, member) in members
        return {
            "id": str(identifier),
            "user_id": str(member),
            "join_timestamp": "2026-10-17T00:00:00+00:00",
            "flags": 0,
        }

    http = bot.http
    for name, value in {
        "get_guild": AsyncMock(return_value=payload),
        "get_roles": AsyncMock(return_value=payload["roles"]),
        "get_channel": AsyncMock(
            side_effect=lambda identifier: deepcopy(channels[identifier])
        ),
        "get_member": AsyncMock(
            side_effect=lambda guild, member: member_payload(
                member, roles=[BOT_ROLE] if member == BOT else [ROLE], bot=member == BOT
            )
        ),
        "send_message": AsyncMock(side_effect=send),
        "get_message": AsyncMock(
            side_effect=lambda channel, identifier: deepcopy(flow.messages[identifier])
        ),
        "start_thread_with_message": AsyncMock(side_effect=start),
        "start_thread_without_message": AsyncMock(side_effect=start),
        "logs_from": AsyncMock(side_effect=logs),
        "edit_channel": AsyncMock(side_effect=edit_channel),
        "add_user_to_thread": AsyncMock(
            side_effect=lambda thread, member: members.add((thread, member))
        ),
        "delete_channel": AsyncMock(
            side_effect=lambda identifier, **kwargs: deleted_threads.append(identifier)
        ),
        "request": AsyncMock(side_effect=thread_member),
    }.items():
        monkeypatch.setattr(http, name, value)
    nonces = iter(["ABCDEF123456", "0123456789AB"])
    monkeypatch.setattr(smoke.module.secrets, "token_hex", lambda size: next(nonces))
    answers = ["STORED", "SMOKEABCDEF123456"]
    answers.append(
        "VERIFIED_SMOKEABCDEF123456" if recovery_mode else "REVISED_SMOKEABCDEF123456"
    )
    if extended:
        answers.extend(["VERIFIED_SMOKEABCDEF123456", "STORED", "PRIVATE0123456789AB"])
    flow.answers.extend(answers)
    args = SimpleNamespace(
        guild_id=GUILD,
        channel_id=CHANNEL,
        member_id=MEMBER,
        model="gpt-6.1-sol",
        restriction=str(ROLE),
        installed=False,
        rest_recovery=recovery_mode,
        extended_rest_recovery=extended,
        rest_only=not recovery_mode,
    )
    report = {
        "checks": [],
        "recovery": {},
        "gateway_tested": False,
        "interaction_acknowledgement_tested": False,
        "user_slash_command_tested": False,
        "view_callbacks_tested": False,
    }
    await smoke.actual_run(args, report, tmp_path)
    assert report["status"] == "passed", report.get("failure")
    cap = 6 if extended else 3
    assert (
        len(report["provider"]["attempts"]) == cap and len(flow.provider_calls) == cap
    )
    assert report["provider"]["max_attempts"] == cap
    assert report["provider"]["blocked_attempts"] == 0
    if extended:
        assert report["extended_recovery"]["explicit_private_membership_tested"] is True
    assert report["view_callbacks_tested"] is extended
    assert report["interaction_acknowledgement_tested"] is False
    assert report["user_slash_command_tested"] is False
    assert len(report["application_source"]["sha256"]) == (6 if recovery_mode else 4)
    thread_count = 2 if extended else 1
    assert (
        len(deleted_threads) == thread_count
        and len(set(deleted_threads)) == thread_count
    )
    assert all(item["status"] == "deleted" for item in report["cleanup"])
    if recovery_mode:
        assert bot.get_guild(GUILD) is None
        assert report["recovery"]["worker_lifecycle_tested"] is False
