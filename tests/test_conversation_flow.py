"""Conversation contracts through Pycord parsers and real application handlers.

Only the OpenAI and Discord HTTP boundaries are simulated. These tests do not
claim to exercise a live Discord gateway or manufacture a human slash command.
"""

import asyncio
from collections import deque
from copy import deepcopy
import importlib
import pickle
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import discord
from pycord.multicog import Bot
import pytest

GUILD = 1061370086331523176
PARENT = 1471696285433204940
MAINTAINER = 1062146522365571123
CLONE = 1500000000000000002
MEMBER = 1500000000000000003
BOT = 1168110042826940446
BOT_ROLE = 1500000000000000004
STAMP = "2026-10-16T00:00:00+00:00"


def user_payload(identifier, *, bot=False):
    return {
        "id": str(identifier),
        "username": "Bot" if bot else "MaintainerPerson",
        "global_name": None,
        "discriminator": "0",
        "avatar": None,
        "bot": bot,
    }


def member_payload(identifier=MEMBER, *, roles=None, bot=False):
    return {
        "user": user_payload(identifier, bot=bot),
        "roles": [str(value) for value in ([MAINTAINER] if roles is None else roles)],
        "joined_at": STAMP,
        "deaf": False,
        "mute": False,
        "pending": False,
        "nick": None,
        "flags": 0,
    }


def message_payload(
    identifier, channel, content, *, kind=0, roles=None, bot=False, reference=None
):
    data = {
        "id": str(identifier),
        "channel_id": str(channel),
        "guild_id": str(GUILD),
        "author": user_payload(BOT if bot else MEMBER, bot=bot),
        "content": content,
        "timestamp": STAMP,
        "edited_timestamp": None,
        "tts": False,
        "mention_everyone": False,
        "mentions": [],
        "mention_roles": [],
        "attachments": [],
        "embeds": [],
        "pinned": False,
        "type": kind,
        "flags": 0,
        "components": [],
    }
    if not bot:
        data["member"] = member_payload(roles=roles)
    if reference is not None:
        data["message_reference"] = {
            "message_id": str(reference),
            "channel_id": str(channel),
            "guild_id": str(GUILD),
        }
    return data


@pytest.fixture
async def conversation(monkeypatch, tmp_path):
    monkeypatch.setenv("ALLOWED_GUILDS", str(GUILD))
    monkeypatch.setenv("BOT_ACCESS_ROLE_IDS", str(MAINTAINER))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SHARE_DIR", str(tmp_path))
    monkeypatch.setenv("IMAGE_SAVE_PATH", str(tmp_path / "images"))
    monkeypatch.setenv("OPENAI_API_KEY", "offline-bot-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://provider.invalid/v1")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    module = importlib.import_module("cogs.text_service_cog")
    text_service = importlib.import_module("services.text_service")
    openai_model = importlib.import_module("models.openai_model")
    usage_module = importlib.import_module("services.usage_service")
    monkeypatch.setattr(openai_model, "SETTINGS_DB", {})
    monkeypatch.setattr(usage_module, "_load_tokenizer", lambda: None)
    monkeypatch.setattr(openai_model.EnvService, "get_http_proxy", lambda _: None)
    for name, value in {
        "USER_INPUT_API_KEYS": False,
        "PRE_MODERATE": False,
        "FORCE_ENGLISH": False,
        "BOT_TAGGABLE": False,
        "BOT_NAME": "GPTBot",
        "original_message": {},
    }.items():
        monkeypatch.setattr(module, name, value)
    monkeypatch.setattr(text_service, "PRE_MODERATE", False)
    monkeypatch.setattr(text_service, "BOT_NAME", "GPTBot")

    provider_answers, provider_calls = deque(), []

    class ProviderResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def json(self):
            assert provider_answers, "Unexpected provider request"
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": provider_answers.popleft(),
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 30,
                    "completion_tokens": 8,
                    "total_tokens": 38,
                },
            }

    class ProviderSession:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def post(self, url, **kwargs):
            assert url == "https://provider.invalid/v1/chat/completions"
            provider_calls.append(deepcopy(kwargs))
            return ProviderResponse()

    monkeypatch.setattr(openai_model.aiohttp, "ClientSession", ProviderSession)
    bot = Bot(intents=discord.Intents.all(), auto_sync_commands=False)
    state = bot._connection
    state.user = discord.ClientUser(state=state, data=user_payload(BOT, bot=True))
    ordinary_permissions = discord.Permissions(
        view_channel=True,
        send_messages=True,
        send_messages_in_threads=True,
        read_message_history=True,
        embed_links=True,
    )
    bot_permissions = discord.Permissions(
        view_channel=True,
        send_messages=True,
        send_messages_in_threads=True,
        read_message_history=True,
        create_public_threads=True,
        manage_threads=True,
        embed_links=True,
        attach_files=True,
    )

    def role(identifier, name, permissions, position):
        return {
            "id": str(identifier),
            "name": name,
            "permissions": str(permissions.value),
            "position": position,
            "color": 0,
            "hoist": False,
            "managed": False,
            "colors": {
                "primary_color": 0,
                "secondary_color": None,
                "tertiary_color": None,
            },
            "mentionable": False,
        }

    guild = discord.Guild(
        state=state,
        data={
            "id": str(GUILD),
            "name": "Conversation Test Guild",
            "owner_id": str(MEMBER + 100),
            "roles": [
                role(GUILD, "@everyone", ordinary_permissions, 0),
                role(MAINTAINER, "Maintainer", discord.Permissions.none(), 1),
                role(CLONE, "Maintainer", discord.Permissions(administrator=True), 2),
                role(BOT_ROLE, "Bot", bot_permissions, 3),
            ],
            "members": [
                member_payload(),
                member_payload(BOT, roles=[BOT_ROLE], bot=True),
            ],
            "channels": [
                {
                    "id": str(PARENT),
                    "guild_id": str(GUILD),
                    "name": "gpt",
                    "type": 0,
                    "position": 0,
                    "permission_overwrites": [],
                    "nsfw": False,
                }
            ],
            "threads": [],
            "emojis": [],
            "features": [],
            "member_count": 2,
            "verification_level": 0,
            "default_message_notifications": 0,
            "explicit_content_filter": 0,
            "mfa_level": 0,
            "afk_timeout": 300,
        },
    )
    state._guilds[GUILD] = guild
    parent = guild.get_channel(PARENT)
    user = guild.get_member(MEMBER)
    assert isinstance(parent, discord.TextChannel)
    assert isinstance(user, discord.Member)
    for permission in (
        "view_channel",
        "send_messages",
        "create_public_threads",
        "send_messages_in_threads",
        "read_message_history",
    ):
        assert getattr(parent.permissions_for(guild.me), permission)

    sent, edits, deleted, messages = [], [], [], {}
    sequence = iter(range(1500000000000000100, 1500000000000010000))

    async def send_message(channel, content=None, **kwargs):
        identifier = next(sequence)
        data = message_payload(identifier, channel, content or "", bot=True)
        data["embeds"] = kwargs.get("embeds") or (
            [kwargs["embed"]] if kwargs.get("embed") else []
        )
        data["components"] = kwargs.get("components") or []
        if kwargs.get("message_reference"):
            data["message_reference"] = kwargs["message_reference"]
        messages[identifier] = deepcopy(data)
        sent.append(deepcopy(data))
        return data

    async def edit_message(channel, identifier, **kwargs):
        data = deepcopy(messages[identifier])
        data.update(kwargs)
        data["edited_timestamp"] = STAMP
        messages[identifier] = deepcopy(data)
        edits.append(deepcopy(data))
        return data

    async def start_thread(channel, identifier, **kwargs):
        data = {
            "id": str(identifier),
            "guild_id": str(GUILD),
            "parent_id": str(channel),
            "name": kwargs["name"],
            "type": 11,
            "owner_id": str(BOT),
            "message_count": 0,
            "member_count": 1,
            "thread_metadata": {
                "archived": False,
                "auto_archive_duration": 60,
                "archive_timestamp": STAMP,
                "locked": False,
            },
        }
        thread = discord.Thread(guild=guild, state=state, data=deepcopy(data))
        guild._threads[thread.id] = thread
        return data

    monkeypatch.setattr(state.http, "send_message", AsyncMock(side_effect=send_message))
    monkeypatch.setattr(state.http, "edit_message", AsyncMock(side_effect=edit_message))
    monkeypatch.setattr(
        state.http,
        "delete_message",
        AsyncMock(
            side_effect=lambda channel, identifier, **kwargs: deleted.append(identifier)
        ),
    )
    monkeypatch.setattr(state.http, "send_typing", AsyncMock())
    monkeypatch.setattr(
        state.http, "start_thread_with_message", AsyncMock(side_effect=start_thread)
    )
    usage = usage_module.UsageService(tmp_path)
    model = openai_model.Model(usage)
    model.model = (
        openai_model.Models.GPT_6_LUNA
    )  # Different from the selected conversation model.
    model.max_tokens = 256
    cog = module.GPT3ComCon(
        bot,
        usage,
        model,
        asyncio.Queue(),
        asyncio.Queue(),
        0,
        0,
        tmp_path,
        None,
        asyncio.Queue(),
    )
    cog.CONVERSATION_STARTER_TEXT_MINIMAL = (
        "You are a helpful assistant. Remember the user's codeword."
    )
    bot.add_cog(cog)
    event_errors = []

    async def on_error(event, *args, **kwargs):
        import sys

        error = sys.exception()
        event_errors.append((event, type(error).__name__))

    monkeypatch.setattr(bot, "on_error", on_error)
    ctx = SimpleNamespace(
        user=user,
        author=user,
        channel=parent,
        guild=guild,
        defer=AsyncMock(),
        respond=AsyncMock(),
        send=parent.send,
    )

    async def start(selected="gpt-6.1-sol"):
        await cog.converse_command(
            ctx,
            draw=False,
            opener=None,
            opener_file=None,
            private=False,
            minimal=True,
            model=selected,
            temperature=None,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
        )
        assert len(cog.conversation_threads) == 1
        identifier = next(iter(cog.conversation_threads))
        assert cog.conversation_threads[identifier].model == selected
        return identifier

    async def deliver(
        content,
        *,
        channel,
        kind=0,
        roles=None,
        identifier=None,
        update=False,
        reference=None,
    ):
        identifier = next(sequence) if identifier is None else identifier
        payload = message_payload(
            identifier, channel, content, kind=kind, roles=roles, reference=reference
        )
        messages[identifier] = deepcopy(payload)
        state.parsers["MESSAGE_UPDATE" if update else "MESSAGE_CREATE"](payload)
        await asyncio.wait_for(asyncio.gather(*tuple(bot._tasks)), timeout=3)
        assert event_errors == [], f"Listener exceptions: {event_errors}"
        return identifier

    flow = SimpleNamespace(
        bot=bot,
        state=state,
        guild=guild,
        cog=cog,
        model=model,
        usage=usage,
        ctx=ctx,
        module=module,
        start=start,
        deliver=deliver,
        sent=sent,
        edits=edits,
        deleted=deleted,
        messages=messages,
        answers=provider_answers,
        provider_calls=provider_calls,
    )
    try:
        yield flow
    finally:
        for view, _ in tuple(state._view_store._views.values()):
            view.stop()
        if bot._tasks:
            await asyncio.gather(*tuple(bot._tasks))
        await bot.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [0, 19], ids=["typed-message", "discord-reply"])
async def test_conversation_reaches_provider_and_discord_for_regular_messages_and_replies(
    conversation, kind
):
    flow = conversation
    channel = await flow.start()
    flow.answers.append("The codeword is ORCHID.")
    prompt = await flow.deliver(
        "Remember that the codeword is ORCHID.",
        channel=channel,
        kind=kind,
        reference=channel if kind == 19 else None,
    )

    assert len(flow.provider_calls) == 1
    request = flow.provider_calls[0]["json"]
    assert request["model"] == "gpt-6.1-sol"
    assert request["model"] != flow.model.model
    assert request["max_completion_tokens"] == 256
    assert request["messages"][0]["role"] == "developer"
    assert request["messages"][-1]["role"] == "user"
    assert "ORCHID" in request["messages"][-1]["content"]
    responses = [
        message
        for message in flow.sent
        if message["content"] == "The codeword is ORCHID."
    ]
    assert len(responses) == 1 and int(responses[0]["channel_id"]) == channel
    assert int(responses[0]["message_reference"]["message_id"]) == prompt
    assert flow.cog.awaiting_responses == flow.cog.awaiting_thread_responses == []
    assert flow.module.original_message[MEMBER] == prompt
    assert await flow.usage.get_usage() > 0


@pytest.mark.asyncio
async def test_multiple_turns_and_edits_preserve_memory_model_and_original_reply(
    conversation,
):
    flow = conversation
    channel = await flow.start()
    flow.answers.extend(
        [
            "I will remember ORCHID.",
            "The remembered codeword is ORCHID.",
            "The replacement codeword is MAPLE.",
        ]
    )
    await flow.deliver("Remember the codeword ORCHID.", channel=channel)
    latest = await flow.deliver(
        "What codeword did I ask you to remember?",
        channel=channel,
        kind=19,
        reference=channel,
    )
    reply = flow.cog.redo_users[MEMBER].response
    await flow.deliver(
        "Replace my codeword with MAPLE.",
        channel=channel,
        identifier=latest,
        kind=19,
        reference=channel,
        update=True,
    )

    assert len(flow.provider_calls) == 3
    second = flow.provider_calls[1]["json"]["messages"]
    assert [message["role"] for message in second] == [
        "developer",
        "user",
        "assistant",
        "user",
    ]
    assert "ORCHID" in second[1]["content"] and "ORCHID" in second[2]["content"]
    revised = flow.provider_calls[2]["json"]["messages"]
    assert "MAPLE" in revised[-1]["content"]
    assert all(
        "What codeword did I ask" not in str(message["content"]) for message in revised
    )
    assert all(call["json"]["model"] == "gpt-6.1-sol" for call in flow.provider_calls)
    assert flow.messages[reply.id]["content"] == "The replacement codeword is MAPLE."
    assert {int(message["id"]) for message in flow.edits} == {reply.id}
    assert len(flow.cog.conversation_threads[channel].history) == 5
    assert flow.cog.awaiting_responses == flow.cog.awaiting_thread_responses == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "roles", [[], [CLONE]], ids=["role-removed", "same-name-admin-role"]
)
async def test_denied_member_messages_never_reach_provider_or_discord(
    conversation, roles
):
    flow = conversation
    channel = await flow.start()
    flow.guild.owner_id = (
        MEMBER  # Ownership and administrator permissions confer no bypass.
    )
    history_before = list(flow.cog.conversation_threads[channel].history)
    sent_before = len(flow.sent)
    await flow.deliver(
        "This message must not spend tokens.",
        channel=channel,
        roles=roles,
        kind=19,
        reference=channel,
    )

    assert not flow.provider_calls
    assert len(flow.sent) == sent_before
    assert flow.cog.conversation_threads[channel].history == history_before
    assert MEMBER not in flow.module.original_message


@pytest.mark.asyncio
async def test_conversation_operates_with_missing_guild_and_role_cache(conversation):
    flow = conversation
    channel = await flow.start()
    flow.answers.extend(["First cacheless response.", "Edited cacheless response."])
    flow.state._guilds.clear()
    prompt = await flow.deliver(
        "A message whose raw member has the required role.", channel=channel
    )
    await flow.deliver(
        "An edited cacheless prompt.", channel=channel, identifier=prompt, update=True
    )
    assert len(flow.provider_calls) == 2
    assert flow.cog.redo_users[MEMBER].response.channel.id == channel
    assert flow.module.original_message[MEMBER] == prompt
    assert any(
        message["content"] == "Edited cacheless response." for message in flow.edits
    )


@pytest.mark.asyncio
async def test_plain_dict_saved_state_can_start_and_answer_a_new_conversation(
    conversation, monkeypatch, tmp_path
):
    flow = conversation
    directory = tmp_path / "pickles"
    directory.mkdir()
    for name in (
        "full_conversation_history",
        "conversation_threads",
        "conversation_thread_owners",
        "instructions",
    ):
        (directory / (name + ".pickle")).write_bytes(pickle.dumps({}))

    async def stop_after_restoring(_):
        raise asyncio.CancelledError()

    with monkeypatch.context() as patch:
        patch.setattr(asyncio, "sleep", stop_after_restoring)
        with pytest.raises(asyncio.CancelledError):
            await flow.cog.on_ready()
    channel = await flow.start()
    flow.answers.append("A restored conversation can answer.")
    await flow.deliver(
        "Test a brand new conversation after restoring saved state.", channel=channel
    )
    assert len(flow.provider_calls) == 1
    assert flow.cog.full_conversation_history[channel]
    assert flow.cog.conversation_thread_owners[MEMBER] == [channel]


@pytest.mark.asyncio
async def test_message_edits_keep_the_personal_api_key_instead_of_billing_the_host(
    conversation, monkeypatch
):
    flow = conversation
    monkeypatch.setattr(flow.module, "USER_INPUT_API_KEYS", True)
    monkeypatch.setattr(flow.module, "USER_KEY_DB", {MEMBER: "offline-member-key"})
    channel = await flow.start()
    flow.answers.extend(["Personal-key answer.", "Personal-key revised answer."])
    prompt = await flow.deliver("Use my personal key for this prompt.", channel=channel)
    await flow.deliver(
        "Use that same personal key for this revision.",
        channel=channel,
        identifier=prompt,
        update=True,
    )
    assert len(flow.provider_calls) == 2
    assert [call["headers"]["Authorization"] for call in flow.provider_calls] == [
        "Bearer offline-member-key"
    ] * 2


@pytest.mark.asyncio
async def test_deleted_personal_key_stops_edit_before_history_or_reply_changes(
    conversation, monkeypatch
):
    flow = conversation
    database = {MEMBER: "offline-member-key"}
    monkeypatch.setattr(flow.module, "USER_INPUT_API_KEYS", True)
    monkeypatch.setattr(flow.module, "USER_KEY_DB", database)
    channel = await flow.start()
    flow.answers.append("The original answer is preserved.")
    prompt = await flow.deliver(
        "A prompt before deleting my saved key.", channel=channel
    )
    previous_history = list(flow.cog.conversation_threads[channel].history)
    database.clear()

    await flow.deliver(
        "An edit after deleting my saved key.",
        channel=channel,
        identifier=prompt,
        update=True,
    )

    assert len(flow.provider_calls) == 1
    assert not flow.edits
    assert flow.cog.conversation_threads[channel].history == previous_history
    assert any("/setup" in message["content"] for message in flow.sent)


@pytest.mark.asyncio
async def test_revoked_role_cannot_edit_existing_response(conversation):
    flow = conversation
    channel = await flow.start()
    flow.answers.append("An authorized answer.")
    prompt = await flow.deliver("An authorized prompt.", channel=channel)
    previous_history = list(flow.cog.conversation_threads[channel].history)
    sent_before = len(flow.sent)

    await flow.deliver(
        "An edit after losing my role.",
        channel=channel,
        identifier=prompt,
        roles=[],
        update=True,
    )

    assert len(flow.provider_calls) == 1
    assert len(flow.sent) == sent_before and not flow.edits
    assert flow.cog.conversation_threads[channel].history == previous_history


@pytest.mark.asyncio
async def test_real_bot_authored_messages_do_not_enter_human_conversation(conversation):
    flow = conversation
    channel = await flow.start()
    sent_before = len(flow.sent)
    previous_history = list(flow.cog.conversation_threads[channel].history)
    flow.state.parsers["MESSAGE_CREATE"](
        message_payload(
            1500000000000099999,
            channel,
            "A real bot-authored fixture message.",
            bot=True,
        )
    )
    await asyncio.wait_for(asyncio.gather(*tuple(flow.bot._tasks)), timeout=3)
    assert not flow.provider_calls
    assert len(flow.sent) == sent_before
    assert flow.cog.conversation_threads[channel].history == previous_history


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "partial_context", [False, True], ids=["guild-context", "interaction-guild-id"]
)
async def test_conversation_start_without_gateway_guild_stops_before_work_and_explains_installation(
    conversation, monkeypatch, partial_context
):
    flow = conversation
    flow.state._guilds.clear()
    if partial_context:
        flow.ctx.guild = None
        flow.ctx.interaction = SimpleNamespace(guild_id=GUILD)
    key_lookup = AsyncMock()
    file_lookup = AsyncMock()
    monkeypatch.setattr(flow.module, "USER_INPUT_API_KEYS", True)
    monkeypatch.setattr(flow.module.TextService, "get_user_api_key", key_lookup)
    monkeypatch.setattr(flow.cog, "load_file", file_lookup)

    await flow.cog.converse_command(
        flow.ctx,
        draw=False,
        opener="This must not start a provider request.",
        opener_file="must-not-be-read.txt",
        private=False,
        minimal=True,
        model="gpt-6.1-sol",
        temperature=None,
        top_p=None,
        frequency_penalty=None,
        presence_penalty=None,
    )

    flow.ctx.respond.assert_awaited_once()
    response = flow.ctx.respond.await_args
    assert response.kwargs["ephemeral"] is True
    assert "not receiving messages" in response.args[0]
    assert "server administrator" in response.args[0]
    link = re.search(
        r"https://discord\.com/oauth2/authorize\?[^)\s]+", response.args[0]
    )
    assert link, "The private failure must provide an actionable installation link"
    query = parse_qs(urlsplit(link.group()).query)
    assert query["client_id"] == [str(BOT)]
    assert query["guild_id"] == [str(GUILD)]
    assert set(query["scope"][0].split()) == {"bot", "applications.commands"}
    assert query["disable_guild_select"] == ["true"]
    permissions = discord.Permissions(int(query["permissions"][0]))
    assert permissions.administrator is False
    for name in (
        "view_channel",
        "send_messages",
        "send_messages_in_threads",
        "read_message_history",
        "create_public_threads",
        "create_private_threads",
        "embed_links",
        "attach_files",
        "manage_threads",
    ):
        assert getattr(permissions, name), f"The installation must permit {name}"
    flow.ctx.defer.assert_not_awaited()
    key_lookup.assert_not_awaited()
    file_lookup.assert_not_awaited()
    flow.state.http.send_message.assert_not_awaited()
    flow.state.http.start_thread_with_message.assert_not_awaited()
    assert not flow.provider_calls
    assert not flow.cog.conversation_threads
    assert not flow.cog.conversation_thread_owners
    assert not flow.cog.full_conversation_history
