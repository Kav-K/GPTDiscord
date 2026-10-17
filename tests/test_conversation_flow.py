"""Conversation contracts through Pycord parsers and real application handlers.

Only the OpenAI and Discord HTTP boundaries are simulated. These tests do not
claim to exercise a live Discord gateway or manufacture a human slash command.
"""

import asyncio
from collections import deque
from copy import deepcopy
import importlib
import pickle
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
    monkeypatch.delenv("CONVERSATION_RECOVERY_GUILD_IDS", raising=False)
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
        await cog.conversation_recovery.close()
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
async def test_conversation_start_without_gateway_or_recovery_stops_before_work_and_explains_operator_action(
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
    assert "not delivering" in response.args[0]
    assert "operator" in response.args[0] and "recovery" in response.args[0]
    assert "/gpt ask" in response.args[0]
    flow.ctx.defer.assert_not_awaited()
    key_lookup.assert_not_awaited()
    file_lookup.assert_not_awaited()
    flow.state.http.send_message.assert_not_awaited()
    flow.state.http.start_thread_with_message.assert_not_awaited()
    assert not flow.provider_calls
    assert not flow.cog.conversation_threads
    assert not flow.cog.conversation_thread_owners
    assert not flow.cog.full_conversation_history


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_gateway", [False, True])
async def test_private_channel_conversation_is_rejected_before_posts_or_provider_work(
    conversation, monkeypatch, missing_gateway
):
    flow = conversation
    if missing_gateway:
        flow.state._guilds.clear()
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
        opener="This private opener must not become a public channel message.",
        opener_file="must-not-be-read.txt",
        private=True,
        minimal=True,
        model=None,
        temperature=None,
        top_p=None,
        frequency_penalty=None,
        presence_penalty=None,
        use_threads=False,
    )

    flow.ctx.respond.assert_awaited_once()
    response = flow.ctx.respond.await_args
    assert "private thread" in response.args[0]
    assert response.kwargs["ephemeral"] is True
    assert response.kwargs["allowed_mentions"].to_dict()["parse"] == []
    flow.ctx.defer.assert_not_awaited()
    key_lookup.assert_not_awaited()
    file_lookup.assert_not_awaited()
    flow.state.http.send_message.assert_not_awaited()
    flow.state.http.start_thread_with_message.assert_not_awaited()
    assert not flow.provider_calls
    assert not flow.cog.conversation_threads
    assert not flow.cog.conversation_thread_owners
    assert not flow.cog.full_conversation_history


@pytest.fixture
def control_responses(monkeypatch):
    """Keep Pycord's genuine acknowledgement state and simulate only HTTP."""
    from discord.webhook.async_ import async_context

    adapter = SimpleNamespace(
        create_interaction_response=AsyncMock(return_value={"interaction": {}})
    )
    followup = AsyncMock()
    monkeypatch.setattr(discord.Webhook, "send", followup)
    token = async_context.set(adapter)
    try:
        yield SimpleNamespace(
            initial=adapter.create_interaction_response, followup=followup
        )
    finally:
        async_context.reset(token)


def end_context(flow, channel, *, actor=MEMBER):
    interaction = discord.Interaction(
        state=flow.state,
        data={
            "id": "1500000000000000999",
            "type": discord.InteractionType.application_command.value,
            "application_id": str(BOT),
            "token": "offline-control-token",
            "version": 1,
            "guild_id": str(GUILD),
            "channel_id": str(channel),
            "member": member_payload(actor),
            "data": {"id": "1500000000000000998", "name": "gpt", "type": 1},
        },
    )
    return discord.ApplicationContext(flow.bot, interaction)


def install_end_http(flow, monkeypatch, channel, *, before_lookup=None):
    # Reuse the actual Discord-shaped payloads that exercise recovery parsing.
    from tests.test_conversation_recovery import guild_payload, thread_payload

    data = thread_payload(channel)

    async def lookup(identifier):
        if before_lookup:
            before_lookup()
        assert identifier == channel
        return deepcopy(data)

    async def edit(identifier, **kwargs):
        assert identifier == channel
        result = deepcopy(data)
        result["name"] = kwargs.get("name", result["name"])
        result["thread_metadata"]["archived"] = kwargs.get("archived", False)
        return result

    monkeypatch.setattr(flow.state.http, "get_channel", AsyncMock(side_effect=lookup))
    monkeypatch.setattr(flow.state.http, "edit_channel", AsyncMock(side_effect=edit))
    monkeypatch.setattr(
        flow.state.http, "get_guild", AsyncMock(return_value=guild_payload())
    )
    monkeypatch.setattr(
        flow.state.http, "get_roles", AsyncMock(return_value=guild_payload()["roles"])
    )
    monkeypatch.setattr(
        flow.state.http,
        "get_member",
        AsyncMock(
            side_effect=lambda guild, user: member_payload(user, bot=user == BOT)
        ),
    )
    monkeypatch.setattr(
        flow.bot,
        "fetch_channel",
        AsyncMock(side_effect=AssertionError("Use local REST guild")),
    )


@pytest.mark.asyncio
async def test_cacheless_slash_end_acknowledges_then_resolves_and_archives_real_thread(
    conversation, control_responses, monkeypatch
):
    flow = conversation
    channel = await flow.start()
    flow.state._guilds.clear()
    ctx = end_context(flow, channel)
    assert isinstance(ctx.channel, discord.PartialMessageable)
    assert ctx.guild is None
    lock = flow.cog.conversation_recovery.lock_for(channel)
    observations = []

    def before_lookup():
        observations.append(
            (
                ctx.response.is_done(),
                lock.locked(),
                asyncio.current_task() in flow.cog.conversation_recovery._active,
            )
        )

    install_end_http(flow, monkeypatch, channel, before_lookup=before_lookup)

    await flow.cog.end_command(ctx)

    assert observations == [(True, True, True)]
    assert not lock.locked()
    assert flow.bot.get_guild(GUILD) is None
    assert flow.state._guilds == {}
    flow.bot.fetch_channel.assert_not_awaited()
    flow.state.http.edit_channel.assert_awaited_once_with(
        channel, name="Closed-GPT", archived=True, reason=None
    )
    assert channel not in flow.cog.conversation_threads
    assert channel not in flow.cog.conversation_thread_owners[MEMBER]
    assert not flow.provider_calls
    assert any(
        message["embeds"] and message["channel_id"] == str(channel)
        for message in flow.sent
    )
    control_responses.initial.assert_awaited_once()
    payload = control_responses.initial.await_args.kwargs
    assert (
        payload["type"]
        == discord.InteractionResponseType.deferred_channel_message.value
    )
    assert payload["data"] == {"flags": 64}
    control_responses.followup.assert_awaited_once()
    assert "ended" in control_responses.followup.await_args.args[0]
    assert control_responses.followup.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rejection", ["other-owner", "busy-user", "busy-thread", "locked"]
)
async def test_cacheless_slash_end_rejections_are_acknowledged_without_lookup_or_mutation(
    conversation, control_responses, monkeypatch, rejection
):
    flow = conversation
    channel = await flow.start()
    original = flow.cog.conversation_threads[channel]
    flow.state._guilds.clear()
    actor = MEMBER + 1 if rejection == "other-owner" else MEMBER
    ctx = end_context(flow, channel, actor=actor)
    install_end_http(flow, monkeypatch, channel)
    lock = flow.cog.conversation_recovery.lock_for(channel)
    if rejection == "busy-user":
        flow.cog.awaiting_responses.append(MEMBER)
    elif rejection == "busy-thread":
        flow.cog.awaiting_thread_responses.append(channel)
    elif rejection == "locked":
        await lock.acquire()
    try:
        await asyncio.wait_for(flow.cog.end_command(ctx), timeout=1)
    finally:
        if rejection == "locked":
            assert lock.locked()
            lock.release()

    assert ctx.response.is_done()
    control_responses.initial.assert_awaited_once()
    if rejection == "other-owner":
        assert "own" in control_responses.followup.await_args.args[0]
    else:
        assert "wait" in control_responses.initial.await_args.kwargs["data"]["content"]
    flow.state.http.get_channel.assert_not_awaited()
    flow.state.http.edit_channel.assert_not_awaited()
    assert flow.cog.conversation_threads[channel] is original
    assert flow.cog.conversation_thread_owners[MEMBER] == [channel]
    assert not flow.provider_calls


@pytest.mark.asyncio
async def test_cacheless_end_lookup_failure_keeps_conversation_and_sanitizes_followup(
    conversation, control_responses, monkeypatch
):
    flow = conversation
    channel = await flow.start()
    flow.state._guilds.clear()
    ctx = end_context(flow, channel)
    install_end_http(flow, monkeypatch, channel)
    flow.state.http.get_channel.side_effect = OSError("private-provider-details")

    await flow.cog.end_command(ctx)

    assert channel in flow.cog.conversation_threads
    assert flow.cog.conversation_thread_owners[MEMBER] == [channel]
    flow.state.http.edit_channel.assert_not_awaited()
    assert "verify" in control_responses.followup.await_args.args[0]
    assert (
        "private-provider-details" not in control_responses.followup.await_args.args[0]
    )
    assert not flow.cog.conversation_recovery.lock_for(channel).locked()
    assert not flow.provider_calls


@pytest.mark.asyncio
async def test_typed_end_inside_dispatch_lock_archives_without_reacquiring_lock(
    conversation, monkeypatch
):
    flow = conversation
    channel = await flow.start()
    install_end_http(flow, monkeypatch, channel)

    await asyncio.wait_for(flow.deliver("end", channel=channel), timeout=2)

    assert channel not in flow.cog.conversation_threads
    assert channel not in flow.cog.conversation_thread_owners[MEMBER]
    flow.state.http.get_channel.assert_not_awaited()
    flow.state.http.edit_channel.assert_awaited_once()
    assert flow.state.http.edit_channel.await_args.kwargs["archived"] is True
    assert not flow.cog.conversation_recovery.lock_for(channel).locked()
    assert not flow.provider_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("join_fails", [False, True])
async def test_private_conversation_adds_actual_owner_before_registry_or_messages(
    conversation, monkeypatch, join_fails
):
    from tests.test_conversation_recovery import thread_payload

    flow = conversation
    channel = 1500000000000000800
    payload = thread_payload(channel)
    payload["type"] = discord.ChannelType.private_thread.value
    monkeypatch.setattr(
        flow.state.http, "start_thread_without_message", AsyncMock(return_value=payload)
    )
    cleanup = AsyncMock()
    monkeypatch.setattr(flow.state.http, "delete_channel", cleanup)
    observations = []

    async def join(thread, user):
        observations.append(
            (thread, user, len(flow.sent), bool(flow.cog.conversation_threads))
        )
        if join_fails:
            raise OSError("Membership was not established")

    monkeypatch.setattr(
        flow.state.http, "add_user_to_thread", AsyncMock(side_effect=join)
    )
    invocation = flow.cog.converse_command(
        flow.ctx,
        draw=False,
        opener=None,
        opener_file=None,
        private=True,
        minimal=True,
        model=None,
        temperature=None,
        top_p=None,
        frequency_penalty=None,
        presence_penalty=None,
    )
    await invocation
    if join_fails:
        cleanup.assert_awaited_once_with(channel)
        assert not flow.sent
        assert not flow.cog.conversation_threads
        assert not flow.cog.conversation_thread_owners
        flow.ctx.respond.assert_awaited_once()
        assert "wasn't started" in flow.ctx.respond.await_args.args[0]
        assert (
            "Membership was not established" not in flow.ctx.respond.await_args.args[0]
        )
        assert flow.ctx.respond.await_args.kwargs["ephemeral"] is True
    else:
        cleanup.assert_not_awaited()
        assert (
            flow.state.http.start_thread_without_message.await_args.kwargs["type"] == 12
        )
        assert channel in flow.cog.conversation_threads
        assert flow.cog.conversation_thread_owners[MEMBER] == [channel]
        flow.ctx.respond.assert_awaited_once()
        assert str(channel) in flow.ctx.respond.await_args.args[0]
        assert flow.ctx.respond.await_args.kwargs["ephemeral"] is True
    assert observations == [(channel, MEMBER, 0, False)]
    assert not flow.provider_calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "with_opener", [False, True], ids=["setup-only", "with-opener"]
)
async def test_conversation_setup_serializes_incoming_turns_and_rejects_end_controls(
    conversation, control_responses, monkeypatch, with_opener
):
    flow = conversation
    text_service = importlib.import_module("services.text_service")
    setup_started, release_setup = asyncio.Event(), asyncio.Event()
    dispatch_started = asyncio.Event()
    provider_started, release_provider = asyncio.Event(), asyncio.Event()
    channel_ids, provider_prompts = [], []
    active, peak = 0, 0
    send = flow.state.http.send_message.side_effect
    original_provider = flow.model.send_chatgpt_chat_request
    original_dispatch = flow.cog.conversation_recovery.dispatch

    async def blocked_setup(channel, content=None, **kwargs):
        if channel != PARENT and not channel_ids:
            channel_ids.append(channel)
            setup_started.set()
            await release_setup.wait()
        return await send(channel, content, **kwargs)

    async def counted_provider(prompt, *args, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        provider_prompts.append(
            prompt if isinstance(prompt, str) else "".join(item.text for item in prompt)
        )
        try:
            if len(provider_prompts) == 1:
                provider_started.set()
                await release_provider.wait()
            return await original_provider(prompt, *args, **kwargs)
        finally:
            active -= 1

    async def observed_dispatch(message, **kwargs):
        dispatch_started.set()
        return await original_dispatch(message, **kwargs)

    monkeypatch.setattr(
        flow.state.http, "send_message", AsyncMock(side_effect=blocked_setup)
    )
    monkeypatch.setattr(flow.model, "send_chatgpt_chat_request", counted_provider)
    monkeypatch.setattr(flow.cog.conversation_recovery, "dispatch", observed_dispatch)
    flow.answers.extend(
        ["The opening answer.", "The later answer."]
        if with_opener
        else ["The later answer."]
    )
    start = asyncio.create_task(
        flow.cog.converse_command(
            flow.ctx,
            draw=False,
            opener="Opening prompt." if with_opener else None,
            opener_file=None,
            private=False,
            minimal=True,
            model="gpt-6.1-sol",
            temperature=None,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
        )
    )
    incoming = None
    try:
        await asyncio.wait_for(setup_started.wait(), timeout=1)
        channel = channel_ids[0]
        thread = flow.cog.conversation_threads[channel]
        lock = flow.cog.conversation_recovery.lock_for(channel)
        assert lock.locked()
        assert start in flow.cog.conversation_recovery._active
        assert flow.cog.conversation_thread_owners[MEMBER] == [channel]
        assert not provider_prompts
        assert not flow.cog.awaiting_responses
        assert not flow.cog.awaiting_thread_responses

        # The registry is already visible, but neither external control may close
        # it while setup is in progress, even before the opener sets busy flags.
        slash = end_context(flow, channel)
        await flow.cog.end_command(slash)
        component = discord.Interaction(
            state=flow.state,
            data={
                "id": "1500000000000000997",
                "type": 3,
                "application_id": str(BOT),
                "token": "offline-setup-button",
                "version": 1,
                "guild_id": str(GUILD),
                "channel_id": str(channel),
                "member": member_payload(),
                "data": {"custom_id": "conversation_end", "component_type": 2},
            },
        )
        await text_service.EndConvoButton(flow.cog).callback(component)
        assert slash.response.is_done() and component.response.is_done()
        assert control_responses.initial.await_count == 2
        for call in control_responses.initial.await_args_list:
            assert call.kwargs["data"]["flags"] & 64
            assert "wait" in call.kwargs["data"]["content"]
        assert flow.cog.conversation_threads[channel] is thread

        incoming = asyncio.create_task(
            flow.deliver("Following prompt.", channel=channel)
        )
        await asyncio.wait_for(dispatch_started.wait(), timeout=1)
        assert not incoming.done()
        assert not provider_prompts
        release_setup.set()
        await asyncio.wait_for(provider_started.wait(), timeout=1)
        assert active == peak == 1
        if with_opener:
            assert "Opening prompt." in provider_prompts[0]
            assert "Following prompt." not in provider_prompts[0]
            assert not incoming.done()
        release_provider.set()
        await asyncio.wait_for(asyncio.gather(start, incoming), timeout=2)

        assert peak == 1
        assert len(flow.provider_calls) == (2 if with_opener else 1)
        assert flow.cog.conversation_threads[channel] is thread
        history = [item.text for item in thread.history]
        if with_opener:
            assert len(history) == 5
            assert "Opening prompt." in history[1]
            assert "The opening answer." in history[2]
            assert "Following prompt." in history[3]
            assert "The later answer." in history[4]
            # The queued turn sees the completed opener's actual assistant answer.
            assert any(
                item["role"] == "assistant" and "The opening answer." in item["content"]
                for item in flow.provider_calls[-1]["json"]["messages"]
            )
        else:
            assert len(history) == 3
            assert "Following prompt." in history[1]
            assert "The later answer." in history[2]
        assert not flow.cog.awaiting_responses
        assert not flow.cog.awaiting_thread_responses
        assert not lock.locked()
        assert start not in flow.cog.conversation_recovery._active
    finally:
        release_setup.set()
        release_provider.set()
        for task in (start, incoming):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (start, incoming) if task is not None),
            return_exceptions=True,
        )


@pytest.mark.asyncio
async def test_cancelled_opener_releases_owner_flags_lock_and_shutdown_registration(
    conversation, monkeypatch
):
    flow = conversation
    entered = asyncio.Event()
    original_provider = flow.model.send_chatgpt_chat_request

    async def blocked_provider(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(flow.model, "send_chatgpt_chat_request", blocked_provider)
    start = asyncio.create_task(
        flow.cog.converse_command(
            flow.ctx,
            draw=False,
            opener="An opening prompt that is cancelled.",
            opener_file=None,
            private=False,
            minimal=True,
            model="gpt-6.1-sol",
            temperature=None,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        channel = next(iter(flow.cog.conversation_threads))
        lock = flow.cog.conversation_recovery.lock_for(channel)
        assert MEMBER in flow.cog.awaiting_responses
        assert channel in flow.cog.awaiting_thread_responses
        assert lock.locked()
        assert start in flow.cog.conversation_recovery._active
        start.cancel()
        with pytest.raises(asyncio.CancelledError):
            await start
        assert channel in flow.cog.conversation_threads
        assert not flow.cog.awaiting_responses
        assert not flow.cog.awaiting_thread_responses
        assert not lock.locked()
        assert start not in flow.cog.conversation_recovery._active
        assert not flow.provider_calls

        monkeypatch.setattr(flow.model, "send_chatgpt_chat_request", original_provider)
        flow.answers.append("A later turn can proceed.")
        await asyncio.wait_for(
            flow.deliver("Continue after cancellation.", channel=channel), timeout=2
        )
        assert len(flow.provider_calls) == 1
        assert (
            "A later turn can proceed."
            in flow.cog.conversation_threads[channel].history[-1].text
        )
        assert not flow.cog.awaiting_responses
        assert not flow.cog.awaiting_thread_responses
    finally:
        if not start.done():
            start.cancel()
        await asyncio.gather(start, return_exceptions=True)
