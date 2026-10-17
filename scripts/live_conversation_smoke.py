"""Opt-in gateway preflight and hybrid conversation smoke; never a CI test.

The Discord thread, fixture messages, replies and OpenAI requests are real. Human
message authorship and delivery are reconstructed through Pycord's event parser;
this does not validate a user slash command. By default a separate SDK-only
gateway preflight must pass before the hybrid application checks can run.
The explicit --rest-recovery mode instead requires evidence of a missing
Gateway guild, then exercises the production REST recovery service without
adding that guild to the Gateway cache. Its human message authorship remains
simulated through a bounded adapter over this test's own Discord fixtures.
"""

import argparse
import asyncio
from contextlib import redirect_stderr, redirect_stdout
import copy
import datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import secrets
import sqlite3
import sys
import tempfile
from urllib.request import getproxies, proxy_bypass

MAX_REQUESTS = 3
EXTENDED_MAX_REQUESTS = 6
OUTPUT_TOKENS = 512
MAX_INPUT_BYTES = 12_000
MAX_TOTAL_INPUT_BYTES = 30_000
EXTENDED_MAX_TOTAL_INPUT_BYTES = 60_000
GATEWAY_TIMEOUT = 90
GATEWAY_ECHO_TIMEOUT = 12
GATEWAY_REST_TIMEOUT = 8
GATEWAY_CLOSE_TIMEOUT = 5
USAGE_FIELDS = {
    "prompt_tokens",
    "completion_tokens",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cached_tokens",
    "reasoning_tokens",
}


class SmokeFailure(RuntimeError):
    """A fixed, credential-free failure category suitable for the report."""


class DiscardOutput:
    """Application logging may include prompts, responses or exception bodies."""

    def write(self, value):
        return len(value)

    def flush(self):
        pass


def limits_for(args):
    extended = getattr(args, "extended_rest_recovery", False)
    require(
        not extended or getattr(args, "rest_recovery", False),
        "extended_requires_rest_recovery",
    )
    return {
        "requests": EXTENDED_MAX_REQUESTS if extended else MAX_REQUESTS,
        "input_bytes": (
            EXTENDED_MAX_TOTAL_INPUT_BYTES if extended else MAX_TOTAL_INPUT_BYTES
        ),
        "timeout": 720 if extended else 480,
    }


class SyntheticAcknowledgement:
    """Local callback acknowledgement; never sends an interaction webhook."""

    def __init__(self):
        self.done = False
        self.count = 0
        self.private_only = True

    def is_done(self):
        return self.done

    async def send_message(self, *args, **kwargs):
        self.done = True
        self.count += 1
        self.private_only &= kwargs.get("ephemeral") is True

    async def defer(self, **kwargs):
        self.done = True
        self.count += 1
        self.private_only &= kwargs.get("ephemeral") is True

    async def send(self, *args, **kwargs):
        await self.send_message(*args, **kwargs)


def control_interaction(bot, thread, response, member, custom_id):
    """Real SDK context with synthetic acknowledgements, no human interaction."""
    import discord

    interaction = discord.Interaction(
        state=bot._connection,
        data={
            "id": str(response.id),
            "type": 3,
            "application_id": str(bot.user.id),
            "token": "synthetic-smoke-no-webhook-token",
            "version": 1,
            "guild_id": str(thread.guild.id),
            "channel_id": str(thread.id),
            "member": copy.deepcopy(member),
            "data": {"custom_id": custom_id, "component_type": 2},
        },
    )
    interaction._guild = thread.guild
    interaction.channel = thread
    interaction.user = discord.Member(
        data=member, guild=thread.guild, state=bot._connection
    )
    interaction.message = response
    acknowledgement = SyntheticAcknowledgement()
    interaction._cs_response = interaction._cs_followup = acknowledgement
    return interaction, acknowledgement


class OwnedDiscordResources:
    """Track exact fixture IDs and reverify ownership before every deletion."""

    def __init__(self, bot, guild_id, parent_id):
        self.bot, self.guild_id, self.parent_id = bot, guild_id, parent_id
        self.threads = {}
        self.starters = {}

    def thread(self, payload, label="owned_thread"):
        import discord

        identifier = int(payload["id"])
        require(
            int(payload["owner_id"]) == self.bot.user.id
            and int(payload["parent_id"]) == self.parent_id
            and int(payload["guild_id"]) == self.guild_id
            and payload["type"]
            in (
                discord.ChannelType.public_thread.value,
                discord.ChannelType.private_thread.value,
            )
            and identifier not in self.threads
            and len(self.threads) < 2,
            "created_thread_scope",
        )
        self.threads[identifier] = label

    def starter(self, identifier):
        require(not self.starters and identifier > 0, "unexpected_second_starter")
        self.starters[identifier] = "owned_starter"

    async def cleanup(self):
        import discord

        results = []
        for identifier, label in self.threads.items():
            try:
                current = await asyncio.wait_for(
                    self.bot.http.get_channel(identifier), 30
                )
                require(
                    int(current["owner_id"]) == self.bot.user.id
                    and int(current["parent_id"]) == self.parent_id
                    and int(current["guild_id"]) == self.guild_id,
                    "cleanup_thread_ownership",
                )
                await asyncio.wait_for(
                    self.bot.http.delete_channel(
                        identifier, reason="GPTDiscord conversation smoke cleanup"
                    ),
                    30,
                )
                results.append({"resource": label, "status": "deleted"})
            except discord.NotFound:
                results.append({"resource": label, "status": "already_absent"})
            except Exception as error:
                results.append(
                    {"resource": label, "status": "failed", **failure_shape(error)}
                )
        for identifier, label in self.starters.items():
            try:
                current = await asyncio.wait_for(
                    self.bot.http.get_message(self.parent_id, identifier), 30
                )
                require(
                    int(current["author"]["id"]) == self.bot.user.id,
                    "cleanup_starter_ownership",
                )
                await asyncio.wait_for(
                    self.bot.http.delete_message(self.parent_id, identifier), 30
                )
                results.append({"resource": label, "status": "deleted"})
            except discord.NotFound:
                results.append({"resource": label, "status": "already_absent"})
            except Exception as error:
                results.append(
                    {"resource": label, "status": "failed", **failure_shape(error)}
                )
        return results


def require(condition, category):
    if not condition:
        raise SmokeFailure(category)


def numeric_usage(value):
    if not isinstance(value, dict):
        return {}
    return {
        key: item
        for key, item in value.items()
        if key in USAGE_FIELDS
        and isinstance(item, int)
        and not isinstance(item, bool)
        and item >= 0
    }


def failure_shape(error):
    # Never serialize error messages, URLs, response bodies or object reprs.
    result = {"category": "client_or_response", "error_type": type(error).__name__}
    if isinstance(error, SmokeFailure):
        result["category"] = str(error)
    elif isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        result["category"] = "timeout"
    status = getattr(error, "status", None)
    if isinstance(status, int):
        result["http_status"] = status
    return result


def restricted_roles(value):
    require(isinstance(value, str), "BOT_ACCESS_ROLE_IDS_required")
    parts = value.split(",")
    require(
        all(part.strip().isdecimal() and int(part.strip()) > 0 for part in parts),
        "BOT_ACCESS_ROLE_IDS_invalid",
    )
    return frozenset(int(part.strip()) for part in parts)


def require_recovery_configuration(args):
    """Require existing operator opt-ins before any recovery diagnostics run."""
    restricted_roles(os.environ.get("BOT_ACCESS_ROLE_IDS"))
    value = os.environ.get("CONVERSATION_RECOVERY_GUILD_IDS")
    require(isinstance(value, str) and bool(value.strip()), "recovery_guilds_required")
    parts = [part.strip() for part in value.split(",")]
    require(
        all(part.isdecimal() and int(part) > 0 for part in parts),
        "recovery_guilds_invalid",
    )
    require(
        args.guild_id in {int(part) for part in parts}, "recovery_target_not_configured"
    )


def missing_gateway_confirmed(report):
    """A failed/missing observation must never masquerade as recovery evidence."""
    details = report.get("gateway", {})
    return (
        details.get("ready") is True
        and details.get("target_guild_cached") is False
        and details.get("client_closed") is True
        and details.get("failure", {}).get("category") == "gateway_target_guild_missing"
        and not any(
            key in details
            for key in ("cleanup_failure", "close_failure", "connection_stop_failure")
        )
        and any(
            payload.get("target_guild_listed") is False
            for payload in details.get("ready_payloads", [])
        )
    )


class StartContext:
    """REST-backed start-handler adapter, explicitly not a slash interaction."""

    def __init__(self, channel, member, payload, on_starter):
        self.channel = channel
        self.guild = channel.guild
        self.user = self.author = member
        self._raw_data = {"guild_id": str(channel.guild.id), "member": payload}
        self.on_starter = on_starter

    async def defer(self, **kwargs):
        pass

    async def respond(self, *args, **kwargs):
        pass

    async def send(self, *args, **kwargs):
        kwargs["content"] = (
            "GPTDiscord temporary conversation smoke. "
            "Inputs below are bot-authored parser fixtures."
        )
        message = await self.channel.send(*args, **kwargs)
        self.on_starter(message.id)
        return message


class RecoveryFixtureReader:
    """Adapt only this smoke's registered bot fixtures after a real REST read.

    Production recovery/authentication code is not patched. Its own fresh member
    lookup still authorizes each reconstructed human message before dispatch.
    """

    def __init__(
        self,
        original,
        *,
        thread_id,
        guild_id,
        bot_id,
        member_id,
        fixtures,
        membership,
        max_fixtures=MAX_REQUESTS,
    ):
        self.original = original
        self.thread_id = thread_id
        self.guild_id = guild_id
        self.bot_id = bot_id
        self.member_id = member_id
        self.fixtures = fixtures
        self.membership = membership
        self.max_fixtures = max_fixtures

    async def __call__(self, channel_id, limit, **kwargs):
        require(channel_id == self.thread_id and 0 < limit <= 20, "recovery_poll_scope")
        require(len(self.fixtures) <= self.max_fixtures, "recovery_fixture_budget")
        payloads = await self.original(channel_id, limit, **kwargs)
        result = []
        for raw in payloads:
            selected = self.fixtures.get(int(raw["id"]))
            if selected is None:
                result.append(raw)
                continue
            require(
                int(raw["channel_id"]) == self.thread_id
                and int(raw["author"]["id"]) == self.bot_id
                and raw["author"].get("bot", False),
                "recovery_fixture_not_bot_owned",
            )
            current = await self.membership()
            require(
                int(current["user"]["id"]) == self.member_id
                and not current["user"].get("bot", False),
                "recovery_fixture_actor",
            )
            result.append(
                {
                    **raw,
                    "guild_id": str(self.guild_id),
                    "content": selected["content"],
                    "author": copy.deepcopy(current["user"]),
                    "member": copy.deepcopy(current),
                }
            )
        return result


class BoundedProvider:
    """Bound actual HTTP attempts, including failures; bypass automatic retries."""

    def __init__(
        self,
        model,
        selected_model,
        *,
        max_requests=MAX_REQUESTS,
        max_input_bytes=MAX_TOTAL_INPUT_BYTES,
    ):
        from models.openai_model import Model

        self.original = Model._post_json.__wrapped__.__get__(model, Model)
        self.selected_model = selected_model
        self.calls = []
        self.total_input_bytes = 0
        self.blocked = 0
        self.max_requests = max_requests
        self.max_input_bytes = max_input_bytes

    async def __call__(self, endpoint, payload, custom_api_key=None):
        input_bytes = len(json.dumps(payload.get("messages", [])).encode("utf-8"))
        roles = [message.get("role") for message in payload.get("messages", [])]
        allowed = (
            endpoint == "chat/completions"
            and payload.get("model") == self.selected_model
            and len(self.calls) < self.max_requests
            and input_bytes <= MAX_INPUT_BYTES
            and self.total_input_bytes + input_bytes <= self.max_input_bytes
            and not payload.get("tools")
            and payload.get("n", 1) == 1
            and bool(roles)
            and all(
                role in {"developer", "system", "user", "assistant", "tool"}
                for role in roles
            )
        )
        if not allowed:
            self.blocked += 1
            raise SmokeFailure("provider_budget_or_model_guard")
        payload = dict(payload)
        for field in ("max_tokens", "max_completion_tokens"):
            if field in payload:
                payload[field] = min(int(payload[field]), OUTPUT_TOKENS)
        require(
            any(field in payload for field in ("max_tokens", "max_completion_tokens")),
            "missing_output_budget",
        )
        self.total_input_bytes += input_bytes
        item = {
            "input_bytes": input_bytes,
            "selected_model": True,
            "message_roles": roles,
        }
        self.calls.append(item)
        try:
            response = await asyncio.wait_for(
                self.original(endpoint, payload, custom_api_key), timeout=90
            )
            item["usage"] = numeric_usage(response.get("usage"))
            item["status"] = "received"
            return response
        except Exception as error:
            item.update(status="failed", **failure_shape(error))
            raise


async def gateway_preflight(args, report):
    """Prove the target guild and own-message delivery with an SDK-only client.

    Import no application modules here: a missing gateway guild must stop before
    the hybrid test can manufacture a REST-populated application cache.
    """
    import discord

    details = {
        "status": "failed",
        "sdk_only": True,
        "ready": False,
        "target_guild_cached": False,
        "echo_seen": False,
        "ready_payloads": [],
        "target_guild_create_seen": False,
        "temporary_message_created": False,
        "temporary_message_deleted": False,
        "client_closed": False,
        "timeout_seconds": GATEWAY_TIMEOUT,
        "echo_timeout_seconds": GATEWAY_ECHO_TIMEOUT,
    }
    report["gateway"] = details
    report["gateway_tested"] = False
    required = restricted_roles(os.environ.get("BOT_ACCESS_ROLE_IDS"))
    require(args.guild_id not in required, "everyone_role_cannot_test_denial")
    intents = discord.Intents.all()  # Match gpt3discord.main's production intents.
    proxy = None if proxy_bypass("discord.com") else getproxies().get("https")
    client = discord.Client(
        intents=intents,
        enable_debug_events=True,
        allowed_mentions=discord.AllowedMentions.none(),
        proxy=proxy,
    )
    details["requested_intents"] = intents.value
    details["proxy_used"] = proxy is not None
    ready = asyncio.Event()
    echo = asyncio.Event()
    received_ids = set()
    seen_dispatches = set()
    posted = None
    connection = None

    def observe(payload):
        if not isinstance(payload, dict) or not isinstance(payload.get("d"), dict):
            return
        kind, sequence, data = payload.get("t"), payload.get("s"), payload["d"]
        if isinstance(sequence, int):
            key = (kind, sequence)
            if key in seen_dispatches:
                return
            if len(seen_dispatches) < 1000:
                seen_dispatches.add(key)
        if kind == "READY" and len(details["ready_payloads"]) < 3:
            guilds = data.get("guilds", [])
            if not isinstance(guilds, list):
                return
            target = next(
                (
                    guild
                    for guild in guilds
                    if isinstance(guild, dict)
                    and str(guild.get("id")) == str(args.guild_id)
                ),
                None,
            )
            details["ready_payloads"].append(
                {
                    "guild_count": len(guilds),
                    "target_guild_listed": target is not None,
                    "target_guild_unavailable": (
                        bool(target.get("unavailable", False))
                        if target is not None
                        else None
                    ),
                }
            )
        elif kind == "GUILD_CREATE" and str(data.get("id")) == str(args.guild_id):
            details["target_guild_create_seen"] = True

    @client.event
    async def on_socket_response(payload):
        observe(payload)

    @client.event
    async def on_socket_raw_receive(payload):
        # Observe SDK-decoded JSON without changing compression or its parser.
        try:
            observe(
                json.loads(payload) if isinstance(payload, (str, bytes)) else payload
            )
        except (TypeError, ValueError):
            details["raw_event_decode_error"] = True

    @client.event
    async def on_ready():
        ready.set()

    @client.event
    async def on_message(message):
        if (
            client.user is not None
            and getattr(getattr(message, "guild", None), "id", None) == args.guild_id
            and message.channel.id == args.channel_id
            and message.author.id == client.user.id
        ):
            if len(received_ids) < 128:
                received_ids.add(message.id)
            if posted is not None and message.id == posted.id:
                echo.set()

    @client.event
    async def on_error(event, *values, **kwargs):
        # SDK default error logging could include an incoming payload or token.
        details["sdk_event_error"] = True

    async def wait_ready():
        waiter = asyncio.create_task(ready.wait())
        try:
            done, _ = await asyncio.wait(
                (connection, waiter), return_when=asyncio.FIRST_COMPLETED
            )
            if connection in done:
                await connection  # Preserve only sanitized failure classifications.
                require(ready.is_set(), "gateway_closed_before_ready")
            await waiter
        finally:
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)

    try:
        async with asyncio.timeout(GATEWAY_TIMEOUT):
            connection = asyncio.create_task(
                client.start(os.environ["DISCORD_TOKEN"], reconnect=False)
            )
            await wait_ready()
            details["ready"] = True
            details["cached_guild_count"] = len(client.guilds)
            details["target_guild_cached"] = client.get_guild(args.guild_id) is not None
            require(client.user is not None and client.user.bot, "bot_token_required")
            require(details["target_guild_cached"], "gateway_target_guild_missing")

            # Check the configured human role before posting any diagnostic.
            roles, member, channel = await asyncio.gather(
                asyncio.wait_for(
                    client.http.get_roles(args.guild_id), GATEWAY_REST_TIMEOUT
                ),
                asyncio.wait_for(
                    client.http.get_member(args.guild_id, args.member_id),
                    GATEWAY_REST_TIMEOUT,
                ),
                asyncio.wait_for(
                    client.fetch_channel(args.channel_id), GATEWAY_REST_TIMEOUT
                ),
            )
            known_roles = {int(role["id"]) for role in roles}
            maintainer_ids = {
                int(role["id"])
                for role in roles
                if role["name"].strip().casefold() == "maintainer"
            }
            member_ids = {int(role) for role in member["roles"]}
            require(required <= known_roles, "configured_role_missing")
            require(
                int(member["user"]["id"]) == args.member_id
                and not member["user"].get("bot", False)
                and bool(required & maintainer_ids & member_ids),
                "configured_maintainer_required",
            )
            require(
                isinstance(channel, discord.TextChannel)
                and channel.guild.id == args.guild_id,
                "text_channel_in_target_guild_required",
            )
            details["maintainer_verified"] = True
            posted = await asyncio.wait_for(
                channel.send(
                    "Temporary GPTDiscord gateway diagnostic; this message will be deleted.",
                    allowed_mentions=discord.AllowedMentions.none(),
                ),
                GATEWAY_REST_TIMEOUT,
            )
            details["temporary_message_created"] = True
            details["temporary_message_id"] = posted.id
            # Gateway delivery may precede the REST send response.
            if posted.id in received_ids:
                echo.set()
            try:
                await asyncio.wait_for(echo.wait(), GATEWAY_ECHO_TIMEOUT)
            except asyncio.TimeoutError as error:
                raise SmokeFailure("gateway_own_message_echo_missing") from error
            details["echo_seen"] = True
            require(
                os.environ.get("BOT_ACCESS_ROLE_IDS") == args.restriction,
                "production_restriction_changed",
            )
            details["status"] = "passed"
    except Exception as error:
        details["failure"] = failure_shape(error)
    finally:
        if posted is not None:
            try:
                require(
                    posted.channel.id == args.channel_id
                    and posted.author.id == client.user.id,
                    "gateway_cleanup_fixture_ownership",
                )
                await asyncio.wait_for(posted.delete(), GATEWAY_REST_TIMEOUT)
                details["temporary_message_deleted"] = True
            except discord.NotFound:
                details["temporary_message_deleted"] = True
            except Exception as error:
                details["status"] = "failed"
                details["cleanup_failure"] = failure_shape(error)
        try:
            await asyncio.wait_for(client.close(), GATEWAY_CLOSE_TIMEOUT)
            details["client_closed"] = True
        except Exception as error:
            details["status"] = "failed"
            details["close_failure"] = failure_shape(error)
        if connection is not None:
            connection.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.gather(connection, return_exceptions=True),
                    GATEWAY_CLOSE_TIMEOUT,
                )
            except Exception as error:
                details["status"] = "failed"
                details["connection_stop_failure"] = failure_shape(error)
    healthy = (
        details["status"] == "passed"
        and details["target_guild_cached"]
        and details["echo_seen"]
        and details["temporary_message_deleted"]
        and details["client_closed"]
    )
    report["gateway_tested"] = healthy
    return healthy


async def run_checks(args, report, directory):
    """Gate application imports and paid calls on the default gateway preflight."""
    limits = limits_for(args)
    if getattr(args, "rest_recovery", False):
        require_recovery_configuration(args)
        await gateway_preflight(args, report)
        report["gateway_tested"] = False
        report["recovery_transport_tested"] = False
        if not missing_gateway_confirmed(report):
            report.update(
                status="failed",
                failure={"category": "recovery_missing_gateway_not_confirmed"},
            )
            report["provider"] = {
                "attempts": [],
                "blocked_attempts": 0,
                "max_attempts": limits["requests"],
            }
            return
        report["recovery"] = {
            "status": "pending",
            "gateway_target_missing_confirmed": True,
            "human_authorship_simulated": True,
            "fixture_transport": "bounded_own_message_rest_adapter",
            "edit_delivery_tested": False,
        }
    elif args.rest_only:
        report["gateway"] = {"status": "skipped", "reason": "explicit_rest_only"}
        report["gateway_tested"] = False
    elif not await gateway_preflight(args, report):
        report.update(status="failed", failure={"category": "gateway_preflight_failed"})
        report["provider"] = {
            "attempts": [],
            "blocked_attempts": 0,
            "max_attempts": limits["requests"],
        }
        return
    await run(args, report, directory)


async def run(args, report, directory):
    import discord

    import cogs.text_service_cog as text_cog
    from models.openai_model import Model
    from services.access_service import message_has_access
    from services.environment_service import EnvService
    from services.usage_service import UsageService

    recovery_mode = getattr(args, "rest_recovery", False)
    extended = getattr(args, "extended_rest_recovery", False)
    limits = limits_for(args)

    modules = {
        "conversation_cog": text_cog,
        "text_service": sys.modules["services.text_service"],
        "openai_model": sys.modules["models.openai_model"],
        "pickle_service": sys.modules["services.pickle_service"],
    }
    if recovery_mode:
        modules.update(
            conversation_recovery=sys.modules["services.conversation_recovery_service"],
            request_context=sys.modules["services.request_context"],
        )
    report["application_source"] = {
        "sha256": {
            name: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
            for name, module in modules.items()
        },
        "all_modules_installed_under_interpreter_prefix": all(
            Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
            for module in modules.values()
        ),
    }
    if getattr(args, "installed", False):
        require(
            report["application_source"][
                "all_modules_installed_under_interpreter_prefix"
            ],
            "installed_application_source_required",
        )

    required = restricted_roles(os.environ.get("BOT_ACCESS_ROLE_IDS"))
    require(args.guild_id not in required, "everyone_role_cannot_test_denial")
    bot = discord.Bot(
        intents=discord.Intents.none(),
        auto_sync_commands=False,
        allowed_mentions=discord.AllowedMentions.none(),
        proxy=EnvService.get_discord_proxy(),
        max_messages=100,
    )
    owned_thread_id = None
    starter_id = None
    provider = None
    cog = None
    recovery = None
    fixture_ids = set()
    recovery_fixtures = {}
    recovery_poll_calls = 0
    resources = OwnedDiscordResources(bot, args.guild_id, args.channel_id)

    def check(name, **details):
        report["checks"].append({"name": name, "status": "passed", **details})

    async def membership():
        # Current REST membership is authoritative before every simulated event.
        payload = await bot.http.get_member(args.guild_id, args.member_id)
        require(int(payload["user"]["id"]) == args.member_id, "member_identity")
        require(not payload["user"].get("bot", False), "human_member_required")
        actual = {int(role) for role in payload["roles"]}
        require(
            bool(actual & required & maintainer_ids), "configured_maintainer_required"
        )
        return payload

    async def dispatch_payload(event, payload):
        nonlocal recovery_poll_calls
        if recovery_mode:
            require(event == "MESSAGE_CREATE", "recovery_does_not_synthesize_edits")
            require(
                int(payload["id"]) in fixture_ids
                and int(payload["channel_id"]) == owned_thread_id
                and int(payload["guild_id"]) == args.guild_id,
                "recovery_fixture_scope",
            )
            message = discord.Message(
                state=bot._connection, channel=thread, data=copy.deepcopy(payload)
            )
            if message.author.bot:
                await recovery.dispatch(message, source="rest")
            else:
                require(message.author.id == args.member_id, "recovery_fixture_actor")
                recovery_fixtures[message.id] = copy.deepcopy(payload)
                require(
                    len(recovery_fixtures) <= limits["requests"],
                    "recovery_fixture_budget",
                )
                recovery_poll_calls += 1
                await asyncio.wait_for(recovery.poll_once(), timeout=120)
            require(
                bot.get_guild(args.guild_id) is None, "recovery_gateway_cache_changed"
            )
            return message
        # Preserve Pycord parsing/cache behavior, then await the registered real
        # cog listener. No gateway connection or fabricated interaction token.
        dispatched = []
        state = bot._connection
        original_dispatch = state.dispatch
        state.dispatch = lambda name, *values: dispatched.append((name, values))
        try:
            state.parsers[event](copy.deepcopy(payload))
        finally:
            state.dispatch = original_dispatch
        expected = "message" if event == "MESSAGE_CREATE" else "message_edit"
        events = [values for name, values in dispatched if name == expected]
        require(len(events) == 1, "parser_listener_event")
        listeners = bot._event_handlers.get("on_" + expected, [])
        require(len(listeners) == 1, "registered_listener")
        values = events[0]
        require(isinstance(values[-1], discord.Message), "parsed_message_type")
        require(isinstance(values[-1].author, discord.Member), "parsed_member_type")
        require(isinstance(values[-1].channel, discord.Thread), "parsed_thread_type")
        for listener in listeners:
            await asyncio.wait_for(listener(*values), timeout=120)
        return values[-1]

    async def fixture(content, *, deny=False, reply_to=None):
        current = await membership()
        options = (
            {"reference": reply_to.to_reference(), "mention_author": False}
            if reply_to
            else {}
        )
        posted = await thread.send("Smoke input fixture: " + content, **options)
        fixture_ids.add(posted.id)
        payload = await bot.http.get_message(thread.id, posted.id)
        if reply_to:
            require(
                payload["type"] == discord.MessageType.reply.value
                and int(payload["message_reference"]["message_id"]) == reply_to.id,
                "genuine_reply_fixture_shape",
            )
        payload.update(guild_id=str(args.guild_id), member=copy.deepcopy(bot_member))
        before = copy.deepcopy(conversation.history)
        calls_before = len(provider.calls)
        genuine = await dispatch_payload("MESSAGE_CREATE", payload)
        require(
            genuine.author.bot
            and not await message_has_access(genuine)
            and len(provider.calls) == calls_before
            and conversation.history == before,
            "genuine_bot_fixture_not_ignored",
        )
        report["bot_authored_fixtures_ignored"] = (
            report.get("bot_authored_fixtures_ignored", 0) + 1
        )
        # A real message ID is necessary for genuine Discord reply references.
        # Discord still records these source fixtures as authored by this bot.
        payload.update(
            guild_id=str(args.guild_id),
            content=content,
            author=copy.deepcopy(current["user"]),
            member=copy.deepcopy(current),
        )
        if deny:
            payload["member"]["roles"] = []
        return payload

    async def response_content():
        require(args.member_id in cog.redo_users, "conversation_response_missing")
        response = cog.redo_users[args.member_id].response
        payload = await bot.http.get_message(thread.id, response.id)
        require(int(payload["author"]["id"]) == bot.user.id, "response_author")
        require(bool(payload.get("content", "").strip()), "empty_discord_output")
        report["discord_replies_live"] = True
        require(
            not cog.awaiting_responses and not cog.awaiting_thread_responses,
            "conversation_wait_state",
        )
        return payload["content"]

    def control_button(response, custom_id):
        view = bot._connection._view_store._synced_message_views.get(response.id)
        require(view is not None, "response_controls_missing")
        matching = [item for item in view.children if item.custom_id == custom_id]
        require(len(matching) == 1, "response_control_missing")
        return matching[0]

    async def invoke_control(button, response):
        interaction, acknowledgement = control_interaction(
            bot, thread, response, await membership(), button.custom_id
        )
        await asyncio.wait_for(button.callback(interaction), timeout=120)
        require(
            acknowledgement.is_done() and acknowledgement.private_only,
            "control_synthetic_private_acknowledgement",
        )
        require(bot.get_guild(args.guild_id) is None, "recovery_gateway_cache_changed")
        report["view_callbacks_tested"] = True
        report["view_callback_invocation"] = (
            "direct_sdk_context_synthetic_acknowledgement"
        )
        return acknowledgement

    async def end_and_check_stale_retry():
        response = cog.redo_users[args.member_id].response
        retry = control_button(response, "conversation_redo")
        end = control_button(response, "conversation_end")
        calls_before = len(provider.calls)
        history_before = copy.deepcopy(conversation.history)
        await invoke_control(end, response)
        remote = await bot.http.get_channel(thread.id)
        require(
            remote["thread_metadata"]["archived"] is True
            and thread.id not in cog.conversation_threads
            and thread.id not in cog.conversation_thread_owners.get(args.member_id, []),
            "end_did_not_close_owned_thread",
        )
        await invoke_control(retry, response)
        require(
            len(provider.calls) == calls_before
            and provider.blocked == 0
            and conversation.history == history_before,
            "stale_retry_after_end_generated",
        )

    async def cleanup():
        results = await resources.cleanup()
        report["cleanup"] = results
        if any(item["status"] == "failed" for item in results):
            report["status"] = "failed"

    try:
        async with asyncio.timeout(limits["timeout"]):
            # login() performs the actual GET /users/@me; never connect gateway.
            await bot.login(os.environ["DISCORD_TOKEN"])
            require(bot.user.bot, "bot_token_required")
            guild_payload = await bot.http.get_guild(args.guild_id)
            role_payload = await bot.http.get_roles(args.guild_id)
            parent_payload = await bot.http.get_channel(args.channel_id)
            require(int(guild_payload["id"]) == args.guild_id, "guild_identity")
            require(
                int(parent_payload["guild_id"]) == args.guild_id
                and parent_payload["type"] == discord.ChannelType.text.value,
                "text_channel_in_target_guild_required",
            )
            role_ids = {int(role["id"]) for role in role_payload}
            require(required <= role_ids, "configured_role_missing")
            maintainer_ids = {
                int(role["id"])
                for role in role_payload
                if role["name"].strip().casefold() == "maintainer"
            }
            current_member = await membership()
            bot_member = await bot.http.get_member(args.guild_id, bot.user.id)
            # Recovery must retain the actual missing-Gateway condition. Local
            # REST objects are sufficient; never add them to the SDK guild cache.
            local_data = {
                **guild_payload,
                "roles": role_payload,
                "channels": [parent_payload],
                "members": [bot_member, current_member],
            }
            if recovery_mode:
                require(
                    bot.get_guild(args.guild_id) is None,
                    "recovery_gateway_cache_required_missing",
                )
                guild = discord.Guild(state=bot._connection, data=local_data)
            else:
                guild = bot._connection._add_guild_from_data(local_data)
            parent = guild.get_channel(args.channel_id)
            require(isinstance(parent, discord.TextChannel), "parsed_parent_type")
            permissions = parent.permissions_for(guild.me)
            require(
                all(
                    getattr(permissions, name)
                    for name in (
                        "view_channel",
                        "send_messages",
                        "create_public_threads",
                        "send_messages_in_threads",
                        "read_message_history",
                        "manage_threads",
                    )
                ),
                "bot_channel_permissions",
            )
            check("rest_identity_and_maintainer", restriction_preserved=True)
            usage = UsageService(directory)
            model = Model(usage)
            model.max_tokens = OUTPUT_TOKENS
            model.summarize_conversations = False
            provider = BoundedProvider(
                model,
                args.model,
                max_requests=limits["requests"],
                max_input_bytes=limits["input_bytes"],
            )
            model._post_json = provider
            cog = text_cog.GPT3ComCon(
                bot,
                usage,
                model,
                asyncio.Queue(),
                asyncio.Queue(),
                None,
                None,
                directory,
                None,
                asyncio.Queue(),
            )
            bot.add_cog(cog)
            if recovery_mode:
                recovery = cog.conversation_recovery
                require(
                    recovery.enabled_for(args.guild_id), "recovery_service_disabled"
                )
                require(
                    recovery.path.parent.resolve() == directory.resolve(),
                    "recovery_database_not_isolated",
                )
                # Each production poll is explicitly driven by this harness.
                # An automatic first poll could consume a bot fixture before
                # its simulated authorship is registered, or overlap a stage.
                recovery.start = lambda: None
                report["recovery"].update(
                    poll_driver="bounded_manual_poll_once",
                    worker_lifecycle_tested=False,
                )
                original_prepare = recovery.prepare_context
                original_watch = recovery.watch_thread
                setup_calls = {"prepare": 0, "watch": 0}

                async def record_prepare(ctx):
                    prepared = await original_prepare(ctx)
                    require(prepared, "recovery_context_not_prepared")
                    setup_calls["prepare"] += 1
                    return prepared

                async def record_watch(target, new=False):
                    require(
                        new and target.id == owned_thread_id, "recovery_watch_scope"
                    )
                    await original_watch(target, new=new)
                    setup_calls["watch"] += 1

                recovery.prepare_context = record_prepare
                recovery.watch_thread = record_watch

            def record_starter(message_id):
                nonlocal starter_id
                require(starter_id is None, "unexpected_second_starter")
                starter_id = message_id
                resources.starter(message_id)
                report["created"] = {"starter_id": starter_id}

            original_start = bot.http.start_thread_with_message

            async def record_thread(channel_id, message_id, **kwargs):
                nonlocal owned_thread_id
                require(
                    channel_id == args.channel_id
                    and message_id == starter_id
                    and owned_thread_id is None,
                    "thread_creation_scope",
                )
                payload = await original_start(channel_id, message_id, **kwargs)
                resources.thread(payload)
                owned_thread_id = int(payload["id"])
                report["created"]["thread_id"] = owned_thread_id
                return payload

            bot.http.start_thread_with_message = record_thread
            context = StartContext(
                parent,
                discord.Member(data=current_member, guild=guild, state=bot._connection),
                current_member,
                record_starter,
            )
            await cog.converse_command(
                context,
                draw=False,
                opener=None,
                opener_file=None,
                private=False,
                minimal=True,
                model=args.model,
                temperature=None,
                top_p=None,
                frequency_penalty=None,
                presence_penalty=None,
                use_threads=True,
            )
            require(owned_thread_id is not None, "public_thread_not_created")
            thread_payload = await bot.http.get_channel(owned_thread_id)
            require(
                int(thread_payload["owner_id"]) == bot.user.id
                and int(thread_payload["parent_id"]) == args.channel_id
                and thread_payload["type"] == discord.ChannelType.public_thread.value,
                "created_thread_scope",
            )
            thread = discord.Thread(
                guild=guild, state=bot._connection, data=thread_payload
            )
            if recovery_mode:
                require(
                    setup_calls == {"prepare": 1, "watch": 1},
                    "recovery_start_handler_setup",
                )
                require(
                    bot.get_guild(args.guild_id) is None,
                    "recovery_gateway_cache_changed",
                )
                require(
                    recovery._cursor(thread.id) == thread.id, "recovery_initial_cursor"
                )
                report["recovery"].update(
                    context_prepared=True,
                    thread_watched=True,
                    database_isolated=True,
                )
                original_logs_from = bot.http.logs_from
                bot.http.logs_from = RecoveryFixtureReader(
                    original_logs_from,
                    thread_id=owned_thread_id,
                    guild_id=args.guild_id,
                    bot_id=bot.user.id,
                    member_id=args.member_id,
                    fixtures=recovery_fixtures,
                    membership=membership,
                    max_fixtures=limits["requests"],
                )
            else:
                guild._add_thread(thread)
            require(
                thread.id in cog.conversation_threads, "conversation_start_rejected"
            )
            conversation = cog.conversation_threads[thread.id]
            require(conversation.model == args.model, "chosen_model_not_stored")
            check("start_handler_selected_model")

            if not recovery_mode:
                denied = await fixture(
                    "This simulated role-denied input must be ignored.", deny=True
                )
                before = copy.deepcopy(conversation.history)
                await dispatch_payload("MESSAGE_CREATE", denied)
                require(
                    not provider.calls and conversation.history == before,
                    "role_denial_reached_provider_or_history",
                )
                check(
                    "role_denied_create", provider_calls=0, role_removal_simulated=True
                )

            word = "SMOKE" + secrets.token_hex(6).upper()
            first = await fixture(
                f"My verification word is {word}. Remember it. Reply only STORED."
            )
            await dispatch_payload("MESSAGE_CREATE", first)
            first_output = await response_content()
            require(
                "STORED" in first_output.upper() and len(provider.calls) == 1,
                "first_turn_contract",
            )
            require(len(conversation.history) == 3, "first_turn_history")
            check(
                "first_turn",
                history_items=len(conversation.history),
                output_characters=len(first_output),
            )

            second = await fixture(
                "Reply with only my verification word from earlier.",
                reply_to=cog.redo_users[args.member_id].response,
            )
            await dispatch_payload("MESSAGE_CREATE", second)
            second_output = await response_content()
            require(
                word in second_output.upper() and len(provider.calls) == 2,
                "multi_turn_memory_contract",
            )
            require(len(conversation.history) == 5, "second_turn_history")
            check(
                "second_turn_memory",
                history_items=len(conversation.history),
                output_characters=len(second_output),
                reconstructed_message_type=19,
            )

            if recovery_mode:
                third = await fixture(
                    "Reply only VERIFIED_ followed by my verification word from earlier.",
                    reply_to=cog.redo_users[args.member_id].response,
                )
                await dispatch_payload("MESSAGE_CREATE", third)
                third_output = await response_content()
                require(
                    "VERIFIED_" + word in third_output.upper()
                    and len(provider.calls) == 3,
                    "recovery_third_turn_memory_contract",
                )
                require(
                    len(conversation.history) == 7 and conversation.count == 3,
                    "recovery_third_turn_history",
                )
                check(
                    "third_turn_memory",
                    history_items=len(conversation.history),
                    output_characters=len(third_output),
                )
                before = copy.deepcopy(conversation.history)
                duplicate = copy.deepcopy(third)
                duplicate["content"] = (
                    "Same message ID with changed create content must not regenerate."
                )
                duplicate = discord.Message(
                    state=bot._connection, channel=thread, data=duplicate
                )
                await recovery.dispatch(duplicate, source="rest")
                await recovery.dispatch(duplicate, source="gateway")
                await recovery.poll_once()
                require(
                    len(provider.calls) == 3 and conversation.history == before,
                    "recovery_duplicate_create_billed",
                )
                check(
                    "recovery_duplicate_create",
                    provider_calls_added=0,
                    changed_content=True,
                    sources=["rest", "gateway"],
                )
                require(recovery_poll_calls == 3, "recovery_poll_count")
                require(
                    recovery._cursor(thread.id) >= int(third["id"]),
                    "recovery_cursor_not_saved",
                )
                require(
                    bot.get_guild(args.guild_id) is None,
                    "recovery_gateway_cache_changed",
                )
                report["recovery"].update(
                    status="passed",
                    poll_once_calls=recovery_poll_calls + 1,
                    durable_cursor_saved=True,
                    duplicate_create_ignored=True,
                )
                report["recovery_transport_tested"] = True
                if extended:
                    public_response = cog.redo_users[args.member_id].response
                    retry = control_button(public_response, "conversation_redo")
                    previous_history = [item.text for item in conversation.history]
                    full_history_size = len(cog.full_conversation_history[thread.id])
                    previous_count = conversation.count
                    await invoke_control(retry, public_response)
                    retried_output = await response_content()
                    require(
                        len(provider.calls) == 4
                        and "VERIFIED_" + word in retried_output.upper()
                        and cog.redo_users[args.member_id].response.id
                        == public_response.id,
                        "retry_visible_answer_contract",
                    )
                    require(
                        provider.calls[3]["message_roles"]
                        == provider.calls[2]["message_roles"]
                        and provider.calls[3]["message_roles"][-1] == "user"
                        and len(provider.calls[3]["message_roles"]) == 6,
                        "retry_payload_includes_previous_answer",
                    )
                    require(
                        len(conversation.history) == len(previous_history)
                        and [item.text for item in conversation.history[:-1]]
                        == previous_history[:-1]
                        and retried_output.strip() in conversation.history[-1].text
                        and conversation.count == previous_count
                        and len(cog.full_conversation_history[thread.id])
                        == full_history_size
                        and retried_output.strip()
                        in cog.full_conversation_history[thread.id][-1],
                        "retry_memory_replacement_contract",
                    )
                    check(
                        "retry_latest_turn",
                        provider_calls_added=1,
                        memory_replaced=True,
                    )
                    await end_and_check_stale_retry()
                    check(
                        "public_end_and_stale_retry",
                        provider_calls_added=0,
                        archived=True,
                    )

                    require(
                        parent.permissions_for(guild.me).create_private_threads,
                        "bot_private_thread_permission",
                    )
                    original_private_start = bot.http.start_thread_without_message

                    async def record_private_thread(channel_id, **kwargs):
                        nonlocal owned_thread_id
                        require(
                            channel_id == args.channel_id
                            and len(resources.threads) == 1,
                            "private_thread_creation_scope",
                        )
                        payload = await original_private_start(channel_id, **kwargs)
                        resources.thread(payload, "owned_private_thread")
                        owned_thread_id = int(payload["id"])
                        report["created"]["private_thread_id"] = owned_thread_id
                        return payload

                    bot.http.start_thread_without_message = record_private_thread
                    private_member = await membership()
                    private_context = StartContext(
                        parent,
                        discord.Member(
                            data=private_member, guild=guild, state=bot._connection
                        ),
                        private_member,
                        record_starter,
                    )
                    await cog.converse_command(
                        private_context,
                        draw=False,
                        opener=None,
                        opener_file=None,
                        private=True,
                        minimal=True,
                        model=args.model,
                        temperature=None,
                        top_p=None,
                        frequency_penalty=None,
                        presence_penalty=None,
                        use_threads=True,
                    )
                    private_data = await bot.http.get_channel(owned_thread_id)
                    require(
                        private_data["type"] == discord.ChannelType.private_thread.value
                        and owned_thread_id in cog.conversation_threads
                        and setup_calls == {"prepare": 2, "watch": 2},
                        "private_thread_start_contract",
                    )
                    thread = discord.Thread(
                        guild=guild, state=bot._connection, data=private_data
                    )
                    from discord.http import Route

                    actual_thread_member = await bot.http.request(
                        Route(
                            "GET",
                            "/channels/{channel_id}/thread-members/{user_id}",
                            channel_id=thread.id,
                            user_id=args.member_id,
                        )
                    )
                    require(
                        int(actual_thread_member.get("user_id", 0)) == args.member_id
                        and int(actual_thread_member.get("id", 0)) == thread.id,
                        "private_owner_not_thread_member",
                    )
                    conversation = cog.conversation_threads[thread.id]
                    require(
                        conversation.model == args.model,
                        "private_chosen_model_not_stored",
                    )
                    recovery_fixtures = {}
                    bot.http.logs_from = RecoveryFixtureReader(
                        original_logs_from,
                        thread_id=thread.id,
                        guild_id=args.guild_id,
                        bot_id=bot.user.id,
                        member_id=args.member_id,
                        fixtures=recovery_fixtures,
                        membership=membership,
                        max_fixtures=limits["requests"],
                    )
                    check(
                        "private_start_and_explicit_membership",
                        thread_type=12,
                        owner_member_verified=True,
                    )
                    private_word = "PRIVATE" + secrets.token_hex(6).upper()
                    private_first = await fixture(
                        f"My private verification word is {private_word}. Remember it. Reply only STORED."
                    )
                    await dispatch_payload("MESSAGE_CREATE", private_first)
                    require(
                        "STORED" in (await response_content()).upper()
                        and len(provider.calls) == 5,
                        "private_first_turn_contract",
                    )
                    private_second = await fixture(
                        "Reply with only my private verification word from earlier.",
                        reply_to=cog.redo_users[args.member_id].response,
                    )
                    await dispatch_payload("MESSAGE_CREATE", private_second)
                    private_output = await response_content()
                    require(
                        private_word in private_output.upper()
                        and word not in private_output.upper()
                        and len(provider.calls) == 6
                        and len(conversation.history) == 5
                        and conversation.count == 2,
                        "private_memory_isolation_contract",
                    )
                    check(
                        "private_two_turn_memory",
                        provider_calls_added=2,
                        history_items=5,
                        isolated=True,
                    )
                    await end_and_check_stale_retry()
                    check(
                        "private_end_and_stale_retry",
                        provider_calls_added=0,
                        archived=True,
                    )
                    report["extended_recovery"] = {
                        "status": "passed",
                        "private_thread_tested": True,
                        "explicit_private_membership_tested": True,
                        "retry_memory_tested": True,
                        "end_callbacks_tested": True,
                        "closed_retry_provider_calls": 0,
                        "acknowledgement_transport": "synthetic_local_adapter",
                    }
                require(
                    os.environ.get("BOT_ACCESS_ROLE_IDS") == args.restriction,
                    "production_restriction_changed",
                )
                require(provider.blocked == 0, "unexpected_provider_attempt")
                report["status"] = "passed"
                return

            # Test revoked-role edit before the authorized edit, using the same
            # cached message ID. The SDK still processes its normal update event.
            denied_edit = copy.deepcopy(second)
            denied_edit["member"] = copy.deepcopy(await membership())
            denied_edit["member"]["roles"] = []
            denied_edit["content"] = "A simulated revoked-role edit must be ignored."
            denied_edit["edited_timestamp"] = datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat()
            before = copy.deepcopy(conversation.history)
            await dispatch_payload("MESSAGE_UPDATE", denied_edit)
            require(
                len(provider.calls) == 2 and conversation.history == before,
                "role_denied_edit_reached_provider_or_history",
            )
            check(
                "role_denied_edit", provider_calls_added=0, role_removal_simulated=True
            )

            edited = copy.deepcopy(second)
            edited["member"] = copy.deepcopy(await membership())
            edited["content"] = (
                "Reply only REVISED_ followed by my verification word from earlier."
            )
            edited["edited_timestamp"] = datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat()
            await dispatch_payload("MESSAGE_UPDATE", edited)
            edited_output = await response_content()
            require(
                "REVISED_" + word in edited_output.upper() and len(provider.calls) == 3,
                "edited_turn_memory_contract",
            )
            require(
                len(conversation.history) == 5 and conversation.count == 2,
                "edited_turn_replaced_history",
            )
            check(
                "edited_turn_memory",
                history_items=len(conversation.history),
                output_characters=len(edited_output),
            )
            require(
                os.environ.get("BOT_ACCESS_ROLE_IDS") == args.restriction,
                "production_restriction_changed",
            )
            require(provider.blocked == 0, "unexpected_provider_attempt")
            report["status"] = "passed"
    except Exception as error:
        report.update(status="failed", failure=failure_shape(error))
    finally:
        report["provider"] = {
            "attempts": provider.calls if provider else [],
            "blocked_attempts": provider.blocked if provider else 0,
            "max_attempts": limits["requests"],
            "max_output_tokens_per_attempt": OUTPUT_TOKENS,
            "max_input_bytes_per_attempt": MAX_INPUT_BYTES,
            "max_total_input_bytes": limits["input_bytes"],
        }
        if recovery is not None:
            try:
                await asyncio.wait_for(recovery.close(), timeout=30)
                report["recovery"]["worker_closed"] = True
            except Exception as error:
                report.update(status="failed", failure=failure_shape(error))
                report["recovery"]["worker_closed"] = False
        await cleanup()
        if cog is not None:
            for view in tuple(
                bot._connection._view_store._synced_message_views.values()
            ):
                view.stop()
        await bot.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Allow paid API requests and temporary Discord writes.",
    )
    parser.add_argument("--guild-id", type=int)
    parser.add_argument("--channel-id", type=int)
    parser.add_argument(
        "--member-id", type=int, help="Actual human with a configured Maintainer role."
    )
    parser.add_argument("--model", default="gpt-6.1-sol")
    transport = parser.add_mutually_exclusive_group()
    transport.add_argument(
        "--rest-only",
        action="store_true",
        help="Debug hybrid REST/parser checks without gateway preflight; gateway remains untested.",
    )
    transport.add_argument(
        "--rest-recovery",
        action="store_true",
        help="Require a missing Gateway guild and exercise explicitly configured REST recovery; human fixture authorship is simulated.",
    )
    parser.add_argument(
        "--extended-rest-recovery",
        action="store_true",
        help="With --rest-recovery, allow up to six paid attempts and test Retry/End callbacks plus a real private conversation. Callback acknowledgements are simulated.",
    )
    parser.add_argument(
        "--installed",
        action="store_true",
        help="Use this interpreter's installed application; requires python -I.",
    )
    parser.add_argument(
        "--report", type=Path, default=Path("conversation-smoke-report.json")
    )
    args = parser.parse_args(argv)
    if not args.live:
        parser.error("--live is required; no calls were made")
    if args.extended_rest_recovery and not args.rest_recovery:
        parser.error("--extended-rest-recovery requires --rest-recovery")
    if args.installed and not sys.flags.isolated:
        parser.error("--installed requires invoking Python with -I")
    if not all(
        value and value > 0
        for value in (args.guild_id, args.channel_id, args.member_id)
    ):
        parser.error("positive --guild-id, --channel-id and --member-id are required")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,99}", args.model):
        parser.error("--model must be a model or deployment ID")
    # Refuse imports into an already-running bot before altering any settings.
    if any(
        name in sys.modules
        for name in (
            "models.openai_model",
            "cogs.text_service_cog",
            "services.environment_service",
        )
    ):
        parser.error("run this script in a fresh process")
    if not all(os.environ.get(name) for name in ("DISCORD_TOKEN", "OPENAI_API_KEY")):
        parser.error(
            "DISCORD_TOKEN and OPENAI_API_KEY must already be in the environment"
        )
    try:
        restricted_roles(os.environ.get("BOT_ACCESS_ROLE_IDS"))
    except SmokeFailure:
        parser.error("a valid existing BOT_ACCESS_ROLE_IDS restriction is required")
    args.restriction = os.environ["BOT_ACCESS_ROLE_IDS"]
    if args.rest_recovery:
        try:
            require_recovery_configuration(args)
        except SmokeFailure:
            parser.error(
                "--rest-recovery requires a valid CONVERSATION_RECOVERY_GUILD_IDS containing --guild-id"
            )
    report_path = args.report.resolve()
    if report_path.exists() or not report_path.parent.is_dir():
        parser.error("--report must name a new file in an existing directory")
    report = {
        "status": "failed",
        "scope": (
            (
                "extended_rest_conversation_recovery"
                if args.extended_rest_recovery
                else "rest_conversation_recovery"
            )
            if args.rest_recovery
            else (
                "hybrid_rest_parser_listener"
                if args.rest_only
                else "gateway_preflight_and_hybrid_rest_parser_listener"
            )
        ),
        "gateway_tested": False,
        "recovery_transport_tested": False,
        "user_slash_command_tested": False,
        "slash_registration_tested": False,
        "interaction_acknowledgement_tested": False,
        "view_callbacks_tested": False,
        "human_gateway_delivery_tested": False,
        "human_authorship_simulated": True,
        "discord_replies_live": False,
        "guild_id": args.guild_id,
        "channel_id": args.channel_id,
        "member_id": args.member_id,
        "requested_application_source": "installed" if args.installed else "checkout",
        "selected_model": args.model,
        "checks": [],
    }
    repository = Path(__file__).resolve().parents[1]
    original_cwd = Path.cwd()
    # Suppress all application logs; only the allowlisted report leaves here.
    logging.disable(logging.CRITICAL)
    with tempfile.TemporaryDirectory(prefix="gptdiscord-conversation-smoke-") as tmp:
        directory = Path(tmp)
        os.chmod(directory, 0o700)
        os.chdir(directory)
        os.environ.update(
            DATA_DIR=tmp,
            SHARE_DIR=tmp,
            IMAGE_SAVE_PATH=str(directory / "images"),
            ALLOWED_GUILDS=str(args.guild_id),
            PYTHON_DOTENV_DISABLED="1",
            ENABLE_INDEXING="false",
            USER_INPUT_API_KEYS="false",
            PRE_MODERATE="false",
            FORCE_ENGLISH="false",
            BOT_TAGGABLE="false",
            HEALTH_SERVICE_ENABLED="false",
        )
        sqlite3.connect(directory / "main_db.sqlite").close()
        if not args.installed:
            sys.path.insert(0, str(repository))
        try:
            with redirect_stdout(DiscardOutput()), redirect_stderr(DiscardOutput()):
                asyncio.run(run_checks(args, report, directory))
        except Exception as error:
            report.update(status="failed", failure=failure_shape(error))
        finally:
            # Close only handles imported by this isolated process.
            for name, attributes in (
                ("models.openai_model", ("SETTINGS_DB",)),
                ("cogs.text_service_cog", ("MOD_DB", "GENERAL_DB", "USER_KEY_DB")),
            ):
                module = sys.modules.get(name)
                for attribute in attributes:
                    handle = getattr(module, attribute, None)
                    if handle is not None:
                        handle.close()
            os.chdir(original_cwd)
    # Exclusive creation avoids replacing unrelated files or following symlinks.
    with report_path.open("x", encoding="utf-8") as handle:
        os.chmod(report_path, 0o600)
        json.dump(report, handle, indent=2)
        handle.write("\n")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
