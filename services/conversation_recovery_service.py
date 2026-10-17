"""Explicit, bounded REST recovery for conversations absent from the Gateway."""

import asyncio
from contextlib import asynccontextmanager, closing
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import time

import discord

from services.access_service import _role_ids, get_access_role_ids, message_has_access
from services.request_context import single_attempt_requests
from utils.safe_ctx_respond import safe_remove_list

logger = logging.getLogger(__name__)
POLL_BATCH = 20
MAX_THREADS = 20
MAX_CONCURRENT_THREADS = 4
HTTP_TIMEOUT = 15


def configured_guilds():
    value = os.getenv("CONVERSATION_RECOVERY_GUILD_IDS", "")
    if not value.strip():
        return frozenset()
    parts = [part.strip() for part in value.split(",")]
    if any(not part.isdecimal() or int(part) <= 0 for part in parts):
        raise ValueError(
            "CONVERSATION_RECOVERY_GUILD_IDS must contain positive guild IDs"
        )
    return frozenset(map(int, parts))


def guild_id_of(message):
    raw = getattr(message, "_raw_data", None) or {}
    value = raw.get("guild_id") or getattr(getattr(message, "guild", None), "id", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def content_revision_of(message):
    payload = {
        "content": message.content,
        "attachments": [str(attachment.id) for attachment in message.attachments],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def revision_of(message):
    payload = {
        "content_revision": content_revision_of(message),
        "edited_at": str(getattr(message, "edited_at", None)),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class ConversationRecoveryService:
    def __init__(self, cog, data_path, guild_ids=None, interval=3.0):
        self.cog = cog
        self.bot = cog.bot
        self.guild_ids = (
            configured_guilds() if guild_ids is None else frozenset(guild_ids)
        )
        self.interval = max(3.0, interval)
        self.path = Path(data_path) / "conversation_recovery.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._locks = {}
        self._task = None
        self._closed = False
        self._active = set()
        self._channels = {}
        self._guilds = {}
        self._routes = {}
        self._offset = 0
        self._retry_after = {}
        self._poll_tasks = {}
        self._poll_locks = {}
        self._poll_slots = asyncio.Semaphore(MAX_CONCURRENT_THREADS)
        self._malformed_notice_after = 0
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS claims (message_id TEXT, revision TEXT, PRIMARY KEY(message_id, revision))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS cursors (thread_id TEXT PRIMARY KEY, guild_id TEXT NOT NULL, message_id TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS versions (message_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL, edited_at REAL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS channel_watches (channel_id TEXT PRIMARY KEY, guild_id TEXT NOT NULL)"
            )
            self._full_channels = {
                int(channel_id): int(guild_id)
                for channel_id, guild_id in db.execute("SELECT * FROM channel_watches")
            }

    def enabled_for(self, guild_id):
        return (
            guild_id in self.guild_ids
            and get_access_role_ids() is not None
            and self.bot.get_guild(guild_id) is None
        )

    def register_route(self, name, *, channels, owner_id, handler):
        """Register an optional chat's live state, never discover arbitrary channels."""
        if not name or ":" in name or name == "conversation" or name in self._routes:
            raise ValueError("Recovery route names must be unique")
        self._routes[name] = (channels, owner_id, handler)

    def _registered(self, channel_id, route):
        if route == "conversation":
            return channel_id in self.cog.conversation_threads and any(
                channel_id in ids
                for ids in self.cog.conversation_thread_owners.values()
            )
        registration = self._routes.get(route)
        if registration is None:
            return False
        channels, owner_id, _ = registration
        if channel_id not in channels():
            return False
        owner = owner_id(channel_id)
        return (
            isinstance(owner, int)
            and not isinstance(owner, bool)
            and owner > 0
        )

    def _route_for(self, channel_id):
        routes = [
            route
            for route in ("conversation", *self._routes)
            if self._registered(channel_id, route)
        ]
        # Conflicting registries must not pick a provider arbitrarily.
        return routes[0] if len(routes) == 1 else None

    def _owned(self, channel_id):
        return self._route_for(channel_id) is not None

    def _valid_channel(self, data, channel_id, guild_id, route):
        if int(data.get("id", 0)) != channel_id:
            return False
        if data.get("type") in (11, 12):
            return int(data.get("owner_id", 0)) == self.bot.user.id
        return (
            route == "conversation"
            and data.get("type") == 0
            and self._full_channels.get(channel_id) == guild_id
        )

    def _claim(self, message, edited=False):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if (
                not edited
                and db.execute(
                    "SELECT 1 FROM claims WHERE message_id = ?", (str(message.id),)
                ).fetchone()
            ):
                return False
            content_hash = content_revision_of(message)
            edited_at = getattr(message, "edited_at", None)
            edited_at = edited_at.timestamp() if edited_at is not None else None
            previous = db.execute(
                "SELECT content_hash, edited_at FROM versions WHERE message_id = ?",
                (str(message.id),),
            ).fetchone()
            if edited and previous:
                if content_hash == previous[0]:
                    if edited_at is not None and (
                        previous[1] is None or edited_at > previous[1]
                    ):
                        db.execute(
                            "UPDATE versions SET edited_at = ? WHERE message_id = ?",
                            (edited_at, str(message.id)),
                        )
                    return False
                if (
                    edited_at is not None
                    and previous[1] is not None
                    and edited_at < previous[1]
                ):
                    return False
            result = db.execute(
                "INSERT OR IGNORE INTO claims VALUES (?, ?)",
                (str(message.id), revision_of(message)),
            )
            if result.rowcount != 1:
                return False
            db.execute(
                "INSERT INTO versions VALUES (?, ?, ?) ON CONFLICT(message_id) DO UPDATE SET content_hash = excluded.content_hash, edited_at = excluded.edited_at",
                (str(message.id), content_hash, edited_at),
            )
            return True

    @staticmethod
    def _cursor_key(thread_id, route):
        # Keep the original conversation keys so existing cursors migrate in place.
        return str(thread_id) if route == "conversation" else f"{route}:{thread_id}"

    def _cursor(self, thread_id, route="conversation"):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute(
                "SELECT message_id FROM cursors WHERE thread_id = ?",
                (self._cursor_key(thread_id, route),),
            ).fetchone()
        return int(row[0]) if row else None

    def _save_cursor(self, thread_id, guild_id, message_id, route="conversation"):
        key = self._cursor_key(thread_id, route)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT message_id FROM cursors WHERE thread_id = ?", (key,)
            ).fetchone()
            if previous is not None and int(previous[0]) >= message_id:
                return
            db.execute(
                "INSERT INTO cursors VALUES (?, ?, ?) ON CONFLICT(thread_id) DO UPDATE SET message_id = excluded.message_id, guild_id = excluded.guild_id",
                (key, str(guild_id), str(message_id)),
            )

    def _acknowledge(self, thread_id, guild_id, message_id, route="conversation"):
        if (
            self._closed
            or not self.enabled_for(guild_id)
            or self._route_for(thread_id) != route
        ):
            return False
        self._save_cursor(thread_id, guild_id, message_id, route)
        return True

    def _message_records(self, payloads, cursor):
        records = []
        for payload in payloads:
            ids = _role_ids([payload.get("id")]) if isinstance(payload, dict) else None
            if ids is None:
                if time.monotonic() >= self._malformed_notice_after:
                    logger.warning(
                        "Skipped malformed Discord recovery record without a valid message ID"
                    )
                    self._malformed_notice_after = time.monotonic() + 60
                continue
            message_id = next(iter(ids))
            if message_id > cursor:
                records.append((message_id, payload))
        return sorted(records, key=lambda record: record[0])

    async def _http(self, awaitable):
        return await asyncio.wait_for(awaitable, HTTP_TIMEOUT)

    async def _fresh_member(self, message, guild_id):
        try:
            member = await self._http(
                self.bot.http.get_member(guild_id, message.author.id)
            )
        except discord.NotFound as error:
            if error.code == 10007:  # Unknown Member: the author has left this guild.
                return False
            raise
        if (
            int(member["user"]["id"]) != message.author.id
            or _role_ids(member.get("roles")) is None
        ):
            raise ValueError("Recovery member identity or roles do not match")
        if member["user"].get("bot", False):
            return False
        raw = getattr(message, "_raw_data", None) or {}
        message._raw_data = {**raw, "guild_id": str(guild_id), "member": member}
        if isinstance(message.guild, discord.Guild):
            message.author = discord.Member(
                data=member, guild=message.guild, state=self.bot._connection
            )
        return await message_has_access(message)

    def lock_for(self, thread_id):
        return self._locks.setdefault(thread_id, asyncio.Lock())

    @asynccontextmanager
    async def active_control(self):
        if self._closed:
            raise RuntimeError("Conversation service is shutting down")
        task = asyncio.current_task()
        already_active = task in self._active
        self._active.add(task)
        try:
            yield
        finally:
            if not already_active:
                self._active.discard(task)

    async def dispatch(
        self, message, *, edited=False, source="gateway", route="conversation"
    ):
        if self._closed or source not in ("gateway", "rest"):
            return None
        if route != "conversation" and (route not in self._routes or edited):
            return None
        if message.author.bot or getattr(message, "webhook_id", None) is not None:
            return None
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return None
        thread_id, guild_id = message.channel.id, guild_id_of(message)
        if source == "rest" and (edited or not self.enabled_for(guild_id)):
            return None
        if source == "rest":
            channel = message.channel
            if isinstance(channel, discord.Thread):
                if channel.type not in (
                    discord.ChannelType.public_thread,
                    discord.ChannelType.private_thread,
                ) or channel.owner_id != self.bot.user.id:
                    return None
            elif not (
                route == "conversation"
                and isinstance(channel, discord.TextChannel)
                and self._full_channels.get(channel.id) == guild_id
            ):
                return None
        lock = self.lock_for(thread_id)
        async with lock:
            if self._closed or self._route_for(thread_id) != route:
                return None
            if source == "rest":
                if not await self._fresh_member(message, guild_id):
                    return None
                if (
                    not self.enabled_for(guild_id)
                    or self._route_for(thread_id) != route
                ):
                    return None
            elif not await message_has_access(message):
                return None
            if not self._claim(message, edited):
                return None
            task = asyncio.current_task()
            self._active.add(task)
            user_waiting = message.author.id in self.cog.awaiting_responses
            thread_waiting = thread_id in self.cog.awaiting_thread_responses
            request_policy = single_attempt_requests.set(source == "rest")
            try:
                if route != "conversation":
                    return await self._routes[route][2](message)
                if edited:
                    return await self.cog._process_message_edit(message)
                return await self.cog._process_message(
                    message, conversation_only=source == "rest"
                )
            finally:
                if not user_waiting:
                    safe_remove_list(self.cog.awaiting_responses, message.author.id)
                if not thread_waiting:
                    safe_remove_list(self.cog.awaiting_thread_responses, thread_id)
                self._active.discard(task)
                single_attempt_requests.reset(request_policy)

    async def _rest_guild(self, guild_id):
        if guild_id not in self._guilds:
            guild, roles, bot_member = await asyncio.gather(
                self._http(self.bot.http.get_guild(guild_id)),
                self._http(self.bot.http.get_roles(guild_id)),
                self._http(self.bot.http.get_member(guild_id, self.bot.user.id)),
            )
            if (
                int(guild["id"]) != guild_id
                or int(bot_member["user"]["id"]) != self.bot.user.id
            ):
                raise ValueError("Recovery guild identity does not match")
            self._guilds[guild_id] = discord.Guild(
                state=self.bot._connection,
                data={**guild, "roles": roles, "members": [bot_member]},
            )
        return self._guilds[guild_id]

    async def prepare_context(self, ctx):
        """Build local REST objects, without populating the Gateway guild cache."""
        guild_id = getattr(ctx.guild, "id", None) or getattr(
            getattr(ctx, "interaction", None), "guild_id", None
        )
        if not self.enabled_for(guild_id):
            return False
        guild, roles, channel, member, bot_member = await asyncio.gather(
            self._http(self.bot.http.get_guild(guild_id)),
            self._http(self.bot.http.get_roles(guild_id)),
            self._http(self.bot.http.get_channel(ctx.channel.id)),
            self._http(self.bot.http.get_member(guild_id, ctx.user.id)),
            self._http(self.bot.http.get_member(guild_id, self.bot.user.id)),
        )
        required = get_access_role_ids()
        if (
            int(guild["id"]) != guild_id
            or int(channel.get("guild_id", 0)) != guild_id
            or int(channel["id"]) != ctx.channel.id
            or channel["type"] != discord.ChannelType.text.value
            or int(member["user"]["id"]) != ctx.user.id
            or member["user"].get("bot", False)
            or int(bot_member["user"]["id"]) != self.bot.user.id
            or not required.intersection(
                {int(role) for role in member["roles"]} | {guild_id}
            )
        ):
            return False
        local_guild = discord.Guild(
            state=self.bot._connection,
            data={
                **guild,
                "roles": roles,
                "channels": [channel],
                "members": [member, bot_member],
            },
        )
        self._guilds[guild_id] = local_guild
        local_channel = local_guild.get_channel(int(channel["id"]))
        local_member = discord.Member(
            data=member, guild=local_guild, state=self.bot._connection
        )
        interaction = getattr(ctx, "interaction", None)
        if interaction is not None:
            interaction._guild = local_guild
            interaction.channel = local_channel
            interaction.user = local_member
        else:
            ctx.guild, ctx.channel = local_guild, local_channel
            ctx.user = ctx.author = local_member
        self.start()
        return True

    async def watch_thread(self, thread, new=False):
        route = self._route_for(thread.id)
        if route is None:
            return
        guild_id = getattr(getattr(thread, "guild", None), "id", None)
        if guild_id is None:
            data = await self._http(self.bot.http.get_channel(thread.id))
            guild_id = int(data.get("guild_id", 0))
            if (
                int(data["id"]) != thread.id
                or data.get("type") not in (11, 12)
                or int(data.get("owner_id", 0)) != self.bot.user.id
                or not self.enabled_for(guild_id)
            ):
                return
            thread.guild = await self._rest_guild(guild_id)
        if (
            not self.enabled_for(guild_id)
            or self._route_for(thread.id) != route
            or not isinstance(thread, discord.Thread)
            or thread.owner_id != self.bot.user.id
        ):
            return
        self._channels[thread.id] = thread
        if new and self._cursor(thread.id, route) is None:
            self._save_cursor(thread.id, guild_id, thread.id, route)
        self.start()

    async def watch_channel(self, channel, new=False):
        """Enroll only a deliberately started full-channel conversation."""
        guild_id = getattr(getattr(channel, "guild", None), "id", None)
        if (
            guild_id not in self.guild_ids
            or get_access_role_ids() is None
            or self._route_for(channel.id) != "conversation"
            or not isinstance(channel, discord.TextChannel)
        ):
            return
        data = await self._http(self.bot.http.get_channel(channel.id))
        if (
            int(data.get("id", 0)) != channel.id
            or int(data.get("guild_id", 0)) != guild_id
            or data.get("type") != 0
        ):
            return
        # A channel predates this session. Its creation ID would replay years of
        # backlog, so baseline at the latest Discord message before enrollment.
        latest = None
        if new or self._cursor(channel.id) is None:
            payloads = await self._http(self.bot.http.logs_from(channel.id, 1))
            latest = int(payloads[0]["id"]) if payloads else channel.id
        if (
            guild_id not in self.guild_ids
            or get_access_role_ids() is None
            or self._route_for(channel.id) != "conversation"
        ):
            return
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "INSERT INTO channel_watches VALUES (?, ?) ON CONFLICT(channel_id) DO UPDATE SET guild_id = excluded.guild_id",
                (str(channel.id), str(guild_id)),
            )
            if latest is not None:
                db.execute(
                    "INSERT INTO cursors VALUES (?, ?, ?) ON CONFLICT(thread_id) DO UPDATE SET message_id = excluded.message_id, guild_id = excluded.guild_id",
                    (str(channel.id), str(guild_id), str(latest)),
                )
        self._full_channels[channel.id] = guild_id
        self._channels[channel.id] = channel
        self.start()

    async def poll_once(self, *, wait=True):
        if self._closed or not any(
            self.enabled_for(guild_id) for guild_id in self.guild_ids
        ):
            return
        self._poll_tasks = {
            thread_id: task
            for thread_id, task in self._poll_tasks.items()
            if not task.done()
        }
        registered = set(self.cog.conversation_threads)
        for channels, _, _ in self._routes.values():
            registered.update(channels())
        ids = [thread_id for thread_id in sorted(registered) if self._owned(thread_id)]
        if not ids:
            return
        offset = self._offset % len(ids)
        created = []
        scanned = 0
        for thread_id in ids[offset:] + ids[:offset]:
            if len(self._poll_tasks) >= MAX_THREADS:
                break
            scanned += 1
            if (
                thread_id in self._poll_tasks
                or time.monotonic() < self._retry_after.get(thread_id, 0)
            ):
                continue
            task = asyncio.create_task(self._poll_thread(thread_id))
            self._poll_tasks[thread_id] = task
            created.append(task)
        self._offset = (offset + scanned) % len(ids)
        if wait:
            try:
                await asyncio.gather(
                    *(asyncio.shield(task) for task in self._poll_tasks.values()),
                    return_exceptions=True,
                )
            except asyncio.CancelledError:
                for task in created:
                    task.cancel()
                await asyncio.gather(*created, return_exceptions=True)
                raise

    async def _poll_thread(self, thread_id):
        lock = self._poll_locks.setdefault(thread_id, asyncio.Lock())
        async with lock, self._poll_slots:
            if self._closed or not any(
                self.enabled_for(guild_id) for guild_id in self.guild_ids
            ):
                return
            try:
                route = self._route_for(thread_id)
                if route is None:
                    return
                data = await self._http(self.bot.http.get_channel(thread_id))
                guild_id = int(data.get("guild_id", 0))
                if (
                    not self.enabled_for(guild_id)
                    or not self._valid_channel(data, thread_id, guild_id, route)
                    or self._route_for(thread_id) != route
                ):
                    return
                cursor = self._cursor(thread_id, route)
                if cursor is None:
                    latest = await self._http(self.bot.http.logs_from(thread_id, 1))
                    # Enrollment uses Discord IDs, independent of the host clock.
                    cursor = int(latest[0]["id"]) if latest else thread_id
                    self._save_cursor(thread_id, guild_id, cursor, route)
                    return
                if data.get("thread_metadata", {}).get("archived", False):
                    return
                channel = self._channels.get(thread_id)
                if (
                    channel is None
                    or not isinstance(channel.guild, discord.Guild)
                    or channel.guild.id != guild_id
                ):
                    guild = await self._rest_guild(guild_id)
                    channel_type = (
                        discord.TextChannel if data["type"] == 0 else discord.Thread
                    )
                    channel = channel_type(
                        guild=guild, state=self.bot._connection, data=data
                    )
                    self._channels[thread_id] = channel
                payloads = await self._http(
                    self.bot.http.logs_from(thread_id, POLL_BATCH, after=cursor)
                )
                for message_id, payload in self._message_records(payloads, cursor):
                    if self._closed:
                        return
                    author = payload.get("author")
                    if not isinstance(author, dict):
                        if not self._acknowledge(thread_id, guild_id, message_id, route):
                            break
                        logger.warning(
                            "Skipped malformed Discord recovery message %s (author)",
                            message_id,
                        )
                        continue
                    if (
                        author.get("bot", False)
                        or payload.get("webhook_id") is not None
                        or payload.get("type") not in (0, 19)
                    ):
                        if not self._acknowledge(thread_id, guild_id, message_id, route):
                            break
                        continue
                    payload = {**payload, "guild_id": str(guild_id)}
                    try:
                        message = discord.Message(
                            state=self.bot._connection, channel=channel, data=payload
                        )
                    except (
                        AttributeError,
                        TypeError,
                        KeyError,
                        ValueError,
                        discord.InvalidData,
                    ) as error:
                        if not self._acknowledge(thread_id, guild_id, message_id, route):
                            break
                        logger.warning(
                            "Skipped unsupported Discord recovery message %s (%s)",
                            payload["id"],
                            type(error).__name__.removesuffix("Error"),
                        )
                        continue
                    await self.dispatch(message, source="rest", route=route)
                    if not self._acknowledge(thread_id, guild_id, message_id, route):
                        break
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._retry_after[thread_id] = time.monotonic() + 60
                logger.warning(
                    "Conversation REST recovery failed for thread %s (%s)",
                    thread_id,
                    type(error).__name__,
                )

    def start(self):
        if (
            self.guild_ids
            and not self._closed
            and (self._task is None or self._task.done())
        ):
            self._task = asyncio.create_task(self._run())
            logger.info(
                "Conversation REST recovery loop started for configured guilds %s",
                ",".join(str(guild_id) for guild_id in sorted(self.guild_ids)),
            )

    async def _run(self):
        while not self._closed and not self.bot.is_closed():
            await self.poll_once(wait=False)
            await asyncio.sleep(self.interval)

    async def close(self):
        self._closed = True
        current = asyncio.current_task()
        polling = set(self._poll_tasks.values()) - {current}
        for task in polling - self._active:
            task.cancel()
        active = self._active - {current}
        if active:
            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        *(asyncio.shield(task) for task in active),
                        return_exceptions=True,
                    ),
                    25,
                )
            except asyncio.TimeoutError:
                for task in active:
                    task.cancel()
                await asyncio.gather(*active, return_exceptions=True)
        for task in polling:
            if not task.done():
                task.cancel()
        await asyncio.gather(*polling, return_exceptions=True)
        self._poll_tasks.clear()
        if self._task is not None:
            if self._task not in self._active:
                self._task.cancel()
            else:
                try:
                    await asyncio.wait_for(asyncio.shield(self._task), 25)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
