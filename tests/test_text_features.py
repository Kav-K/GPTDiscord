"""Original text commands exercise real handlers and Discord response objects."""

from copy import deepcopy
import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from discord.webhook.async_ import async_context
import pytest

from models.user_model import Instruction
from tests.test_conversation_controls import component_interaction
from tests.test_conversation_flow import (
    MEMBER,
    PARENT,
    conversation,
    end_context,
    install_end_http,
    message_payload,
)


class TextAdapter:
    """Only Discord HTTP is simulated; SDK ACK, webhook and file paths are real."""

    def __init__(self, flow, channel):
        self.flow, self.channel = flow, channel
        self.initial, self.followups, self.uploads = {}, [], []

    async def create_interaction_response(
        self, identifier, token, *, type, data=None, **kwargs
    ):
        assert identifier not in self.initial, "Duplicate initial response"
        self.initial[identifier] = {"type": type, "data": deepcopy(data or {})}
        return {"interaction": {}}

    async def execute_webhook(
        self, identifier, token, *, payload=None, multipart=None, files=None, **kwargs
    ):
        assert self.initial, "Webhook sent before acknowledgement"
        if multipart:
            payload = json.loads(
                next(
                    part["value"]
                    for part in multipart
                    if part["name"] == "payload_json"
                )
            )
        self.followups.append(deepcopy(payload))
        data = message_payload(
            1750000000000000000 + len(self.followups),
            self.channel,
            payload.get("content", ""),
            bot=True,
        )
        data.update(webhook_id=str(identifier), flags=payload.get("flags", 0))
        data["embeds"] = payload.get("embeds", [])
        data["components"] = payload.get("components", [])
        for file in files or ():
            self.uploads.append((file.filename, file.fp.read()))
        self.flow.messages[int(data["id"])] = deepcopy(data)
        return data


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parameter",
    [
        "openai_key",
        "openai_base_url",
        "usage_service",
        "reset_settings",
        "_model",
        "__dict__",
        "best_of",
        "mode",
        "use_org",
        "missing",
    ],
)
async def test_settings_reject_credentials_internal_fields_methods_and_retired_options(
    conversation, parameter
):
    flow = conversation
    before = dict(flow.model.__dict__)
    await flow.cog.process_settings(flow.ctx, parameter, "must-not-be-echoed")
    assert flow.model.__dict__ == before
    response = flow.ctx.respond.await_args
    assert response.kwargs["ephemeral"] is True
    assert "supported" in response.args[0]
    assert "must-not-be-echoed" not in response.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parameter,value,expected",
    [
        ("temp", "0.25", 0.25),
        ("max_tokens", "512", 512),
        ("model", "gpt-4.1", "gpt-4.1"),
        ("low_usage_mode", "True", True),
    ],
)
async def test_supported_settings_use_validated_model_properties(
    conversation, parameter, value, expected
):
    flow = conversation
    await flow.cog.process_settings(flow.ctx, parameter, value)
    assert getattr(flow.model, parameter) == expected
    assert "Successfully" in flow.ctx.respond.await_args.args[0]
    assert flow.ctx.respond.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_settings_display_and_autocomplete_offer_same_supported_options(
    conversation,
):
    flow = conversation
    autocomplete = importlib.import_module("models.autocomplete_model")
    await flow.cog.send_settings_text(flow.ctx)
    embed = flow.ctx.respond.await_args.kwargs["embed"]
    displayed = embed.fields[0].value.splitlines()
    choices = await autocomplete.Settings_autocompleter.get_settings(
        SimpleNamespace(value="")
    )
    assert choices == displayed[:25]
    assert {"model", "temp", "max_tokens", "summarize_conversations"} <= set(displayed)
    assert not {
        "openai_key",
        "openai_base_url",
        "usage_service",
        "mode",
        "best_of",
        "use_org",
    }.intersection(displayed)
    assert flow.model.openai_key not in str(embed.to_dict())
    assert "reasoning" in embed.footer.text


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["get", "clear"])
@pytest.mark.parametrize("stored", [False, True])
async def test_instruction_reads_and_clear_do_not_moderate_empty_input(
    conversation, monkeypatch, mode, stored
):
    flow = conversation
    monkeypatch.setattr(flow.module, "PRE_MODERATE", True)
    moderate = AsyncMock(
        side_effect=AssertionError("Read/clear has no text to moderate")
    )
    monkeypatch.setattr(flow.module.Moderation, "simple_moderate_and_respond", moderate)
    flow.ctx.interaction = object()
    paginator = SimpleNamespace(respond=AsyncMock())
    constructor = lambda **kwargs: paginator
    monkeypatch.setattr(flow.module.pages, "Paginator", constructor)
    if stored:
        flow.cog.instructions[MEMBER] = Instruction(MEMBER, "Reply in French.")
    await flow.cog.instruction_command(flow.ctx, mode, "user", None, None, True)
    flow.ctx.defer.assert_awaited_once_with(ephemeral=True)
    moderate.assert_not_awaited()
    if mode == "get" and stored:
        paginator.respond.assert_awaited_once_with(flow.ctx.interaction)
    else:
        flow.ctx.respond.assert_awaited_once()
    if mode == "clear":
        assert MEMBER not in flow.cog.instructions
    assert not flow.provider_calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action",
    [
        "ask",
        "private-ask",
        "ask_gpt_action",
        "paraphrase_action",
        "elaborate_action",
        "summarize_action",
        "edit",
    ],
)
async def test_original_text_commands_reach_chat_provider_and_real_discord_followup(
    conversation, action
):
    flow = conversation
    flow.model.max_tokens = 4000
    flow.cog.instructions[MEMBER] = Instruction(MEMBER, "Use concise sentences.")
    ctx = end_context(flow, PARENT)
    adapter = TextAdapter(flow, PARENT)
    flow.answers.append("The resulting text.")
    source = "Explain how a rainbow forms in the sky."
    message = discord.Message(
        state=flow.state,
        channel=flow.ctx.channel,
        data=message_payload(1650000000000000000, PARENT, source),
    )
    token = async_context.set(adapter)
    try:
        if action in ("ask", "private-ask"):
            await flow.cog.ask_command(ctx, source, private=action == "private-ask")
        elif action == "edit":
            await flow.cog.edit_command(
                ctx, "Make this concise.", source, False, None, None
            )
        else:
            await getattr(flow.cog, action)(ctx, message)
    finally:
        async_context.reset(token)
    assert len(adapter.initial) == 1
    ack = next(iter(adapter.initial.values()))
    assert ack["type"] == 5
    assert bool(ack["data"].get("flags", 0) & 64) == (action == "private-ask")
    assert len(flow.provider_calls) == 1
    payload = flow.provider_calls[0]["json"]
    assert source in payload["messages"][-1]["content"]
    if action != "edit":
        assert any(
            item["content"] == "Use concise sentences." for item in payload["messages"]
        )
    assert len(adapter.followups) == 1
    response = adapter.followups[0]
    assert "The resulting text." in str(
        response.get("content") or response.get("embeds")
    )
    assert flow.cog.redo_users[MEMBER].ctx is ctx
    assert flow.cog.redo_users[MEMBER].standalone_request is True


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_gateway", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
async def test_ended_conversation_download_is_owner_only_private_and_contains_original_transcript(
    conversation, monkeypatch, missing_gateway, legacy
):
    flow = conversation
    channel = await flow.start()
    flow.answers.append("The conversation answer.")
    await flow.deliver("A private codeword: ORCHID.", channel=channel)
    transcript = list(flow.cog.full_conversation_history[channel])
    private_adapter = TextAdapter(flow, channel)
    private_ctx = end_context(flow, channel, actor=MEMBER + 1)
    flow.answers.append("ANOTHER_MEMBER_PRIVATE_ASK")
    private_token = async_context.set(private_adapter)
    try:
        await flow.cog.ask_command(
            private_ctx, "A separate private question.", private=True
        )
    finally:
        async_context.reset(private_token)
    assert flow.cog.full_conversation_history[channel] == transcript
    assert "ANOTHER_MEMBER_PRIVATE_ASK" not in str(
        flow.cog.conversation_threads[channel].history
    )
    if legacy:
        thread = flow.cog.conversation_threads[channel]
        del thread.transcript_start
        flow.cog.full_conversation_history[channel].insert(
            0, "LEGACY_PRIVATE_ASK_SECRET"
        )
        transcript = [item.text for item in thread.history[1:]]
    install_end_http(flow, monkeypatch, channel)
    if missing_gateway:
        flow.state._guilds.clear()
    adapter = TextAdapter(flow, channel)
    token = async_context.set(adapter)
    try:
        await flow.cog.end_command(end_context(flow, channel))
        end_message = next(data for data in reversed(flow.sent) if data["components"])
        views = [
            view
            for view, _ in flow.state._view_store._views.values()
            if isinstance(view, flow.module.ShareView)
        ]
        assert views
        button = views[-1].children[0]
        assert button.label == "Download Conversation"
        assert button.owner_id == MEMBER
        assert button.history == tuple(transcript)
        assert MEMBER not in [
            owner
            for owner, threads in flow.cog.conversation_thread_owners.items()
            if channel in threads
        ]
        # A different allowed member must never receive or export this transcript.
        sdk_message = discord.Message(
            state=flow.state,
            channel=flow.guild.get_thread(channel),
            data=deepcopy(end_message),
        )
        denied = component_interaction(flow, sdk_message, user_id=MEMBER + 1)
        await button.callback(denied)
        assert adapter.initial[denied.id]["data"]["flags"] & 64
        assert "owner" in adapter.initial[denied.id]["data"]["content"]
        assert not adapter.uploads
        # The closed view retains an immutable snapshot after live registry removal.
        flow.cog.full_conversation_history[channel].append("MUST_NOT_LEAK_LATER_DATA")
        allowed = component_interaction(flow, sdk_message)
        await button.callback(allowed)
        assert adapter.initial[allowed.id] == {"type": 5, "data": {"flags": 64}}
    finally:
        async_context.reset(token)
    assert len(adapter.uploads) == 1
    filename, data = adapter.uploads[0]
    assert filename == f"conversation-{channel}.txt"
    expected = "\n\n".join(transcript)
    if legacy:
        expected = (
            "Recovered conversation context. Earlier turns may have been summarized.\n\n"
            + expected
        )
    assert data.decode("utf-8") == expected
    assert b"LEGACY_PRIVATE_ASK_SECRET" not in data
    assert b"ANOTHER_MEMBER_PRIVATE_ASK" not in data
    assert b"ORCHID" in data
    assert b"MUST_NOT_LEAK_LATER_DATA" not in data
    assert adapter.followups[-1]["flags"] & 64
    assert flow.bot.get_guild(flow.guild.id) is (
        None if missing_gateway else flow.guild
    )
    assert len(flow.provider_calls) == 2  # Download never contacts an external API.


@pytest.mark.asyncio
@pytest.mark.parametrize("parameter", ["temp", "top_p", "unknown"])
async def test_freeform_setting_autocomplete_returns_choices_without_invalid_defer(
    conversation, parameter
):
    flow = conversation
    module = importlib.import_module("models.autocomplete_model")
    interaction = discord.Interaction(
        state=flow.state,
        data={
            "id": "1600000000000099999",
            "type": 4,
            "application_id": str(flow.bot.user.id),
            "token": "offline-autocomplete",
            "version": 1,
            "guild_id": str(flow.guild.id),
            "channel_id": str(PARENT),
            "member": {
                "user": {
                    "id": str(MEMBER),
                    "username": "Maintainer",
                    "discriminator": "0",
                    "avatar": None,
                },
                "roles": [],
            },
            "data": {"id": "1600000000000099998", "name": "settings", "type": 1},
        },
    )
    ctx = discord.AutocompleteContext(flow.bot, interaction)
    ctx.options = {"parameter": parameter}
    ctx.value = "0."
    adapter = TextAdapter(flow, PARENT)
    token = async_context.set(adapter)
    try:
        choices = await module.Settings_autocompleter.get_value(ctx)
        assert choices == []
        assert not interaction.response.is_done()
        await interaction.response.send_autocomplete_result(choices=choices)
    finally:
        async_context.reset(token)
    assert adapter.initial[interaction.id] == {"type": 8, "data": {"choices": []}}


@pytest.mark.asyncio
@pytest.mark.parametrize("opener", [False, True], ids=["incoming-turn", "opener"])
@pytest.mark.parametrize(
    "personal", [False, True], ids=["channel-instruction", "user-priority"]
)
async def test_conversation_scoped_instructions_reach_provider_without_changing_starter_or_export(
    conversation, monkeypatch, opener, personal
):
    flow = conversation
    owner_text, channel_text = (
        "Use the user's personal writing style.",
        "Use this channel's writing style.",
    )
    if personal:
        flow.cog.instructions[MEMBER] = Instruction(MEMBER, owner_text)
    original_start = flow.state.http.start_thread_with_message.side_effect

    async def created(parent, identifier, **kwargs):
        result = await original_start(parent, identifier, **kwargs)
        flow.cog.instructions[identifier] = Instruction(identifier, channel_text)
        return result

    monkeypatch.setattr(
        flow.state.http, "start_thread_with_message", AsyncMock(side_effect=created)
    )
    flow.answers.append("An answer following the scoped instruction.")
    if opener:
        await flow.cog.converse_command(
            flow.ctx,
            draw=False,
            opener="A prompt using scoped instructions.",
            opener_file=None,
            private=False,
            minimal=True,
            model="gpt-6.1-sol",
            temperature=None,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
        )
        channel = next(iter(flow.cog.conversation_threads))
    else:
        channel = await flow.start()
        await flow.deliver("A prompt using scoped instructions.", channel=channel)
    assert len(flow.provider_calls) == 1
    messages = flow.provider_calls[0]["json"]["messages"]
    selected, excluded = (
        (owner_text, channel_text) if personal else (channel_text, owner_text)
    )
    assert any(
        item["role"] == "developer" and item["content"] == selected for item in messages
    )
    assert not any(item["content"] == excluded for item in messages)
    starter = flow.cog.conversation_threads[channel].history[0].text
    assert owner_text not in starter and channel_text not in starter
    transcript = str(flow.cog.full_conversation_history[channel])
    assert owner_text not in transcript and channel_text not in transcript
    assert "An answer following the scoped instruction." in transcript


@pytest.mark.asyncio
@pytest.mark.parametrize("format", ["txt", "json"])
@pytest.mark.parametrize("rejected", [False, True])
async def test_saved_opener_is_read_after_key_and_ack_then_moderated_before_thread_creation(
    conversation, monkeypatch, tmp_path, format, rejected
):
    flow = conversation
    path = tmp_path / "openers" / f"saved.{format}"
    path.parent.mkdir()
    content = "A saved opener containing Ω."
    path.write_text(
        (
            json.dumps({"text": content, "temperature": 0.2, "top_p": 0.8})
            if format == "json"
            else content
        ),
        encoding="utf-8",
    )
    ctx = end_context(flow, PARENT)
    events = []

    class KeyDB(dict):
        def get(self, key, default=None):
            assert not ctx.response.is_done()
            events.append("key")
            return super().get(key, default)

    monkeypatch.setattr(flow.module, "USER_INPUT_API_KEYS", True)
    monkeypatch.setattr(
        flow.module, "USER_KEY_DB", KeyDB({MEMBER: "offline-owner-key"})
    )
    monkeypatch.setattr(flow.module, "PRE_MODERATE", True)
    read = flow.cog.load_file

    async def load(opener_path, context):
        assert context is ctx and ctx.response.is_done()
        assert opener_path == path.resolve()
        events.append("file")
        return await read(opener_path, context)

    async def moderate(prompt, context):
        assert context is ctx and ctx.response.is_done()
        assert prompt == content + " Additional prompt."
        assert not flow.cog.conversation_threads
        assert not flow.sent
        events.append("moderate")
        return rejected

    monkeypatch.setattr(flow.cog, "load_file", load)
    monkeypatch.setattr(flow.module.Moderation, "simple_moderate_and_respond", moderate)
    adapter = TextAdapter(flow, PARENT)
    flow.answers.append("An answer to the saved opener.")
    token = async_context.set(adapter)
    try:
        await flow.cog.converse_command(
            ctx,
            draw=False,
            opener=" Additional prompt.",
            opener_file=f"../ignored/saved.{format}",
            private=False,
            minimal=True,
            model="gpt-6.1-sol",
            temperature=0.7,
            top_p=0.6,
            frequency_penalty=None,
            presence_penalty=None,
        )
    finally:
        async_context.reset(token)
    assert events == ["key", "file", "moderate"]
    assert adapter.initial[ctx.interaction.id]["type"] == 5
    if rejected:
        assert not flow.provider_calls
        assert not flow.sent
        assert not flow.cog.conversation_threads
        assert not flow.cog.conversation_thread_owners
        flow.state.http.start_thread_with_message.assert_not_awaited()
    else:
        assert len(flow.provider_calls) == 1
        channel = next(iter(flow.cog.conversation_threads))
        thread = flow.cog.conversation_threads[channel]
        assert thread.has_opener
        assert thread.temperature == (0.2 if format == "json" else 0.7)
        assert thread.top_p == (0.8 if format == "json" else 0.6)
        assert flow.cog.full_conversation_history[channel] == [
            content + " Additional prompt.",
            "An answer to the saved opener.",
        ]
        assert MEMBER in flow.cog.redo_users
        assert not flow.cog.awaiting_responses
        assert not flow.cog.awaiting_thread_responses


@pytest.mark.asyncio
async def test_missing_personal_key_stops_saved_opener_before_file_access_or_thread_creation(
    conversation, monkeypatch
):
    flow = conversation
    monkeypatch.setattr(flow.module, "USER_INPUT_API_KEYS", True)
    monkeypatch.setattr(flow.module, "USER_KEY_DB", {})
    read = AsyncMock(side_effect=AssertionError("Missing key must stop opener reads"))
    monkeypatch.setattr(flow.cog, "load_file", read)
    ctx = end_context(flow, PARENT)
    adapter = TextAdapter(flow, PARENT)
    token = async_context.set(adapter)
    try:
        await flow.cog.converse_command(
            ctx,
            draw=False,
            opener=None,
            opener_file="saved.txt",
            private=False,
            minimal=True,
            model=None,
            temperature=None,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
        )
    finally:
        async_context.reset(token)
    assert adapter.initial[ctx.interaction.id]["type"] == 9
    read.assert_not_awaited()
    assert not flow.sent
    assert not flow.provider_calls
    assert not flow.cog.conversation_threads
    assert not flow.cog.conversation_thread_owners


@pytest.mark.asyncio
async def test_reused_full_channel_records_a_new_transcript_boundary_without_deleting_old_history(
    conversation, monkeypatch
):
    flow = conversation
    monkeypatch.setattr(flow.module, "CHANNEL_CHAT_ROLES", [None])

    async def start():
        await flow.cog.converse_command(
            flow.ctx,
            draw=False,
            opener=None,
            opener_file=None,
            private=False,
            minimal=True,
            model="gpt-6.1-sol",
            temperature=None,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
            use_threads=False,
        )

    await start()
    first = flow.cog.conversation_threads[PARENT]
    assert first.transcript_start == 0
    flow.answers.append("The previous conversation's private answer.")
    await flow.deliver("The previous conversation's question.", channel=PARENT)
    previous = list(flow.cog.full_conversation_history[PARENT])
    await flow.deliver("end", channel=PARENT)
    await start()
    current = flow.cog.conversation_threads[PARENT]
    assert current is not first
    assert current.transcript_start == len(previous) == 2
    flow.answers.append("The current conversation's answer.")
    await flow.deliver("The current conversation's question.", channel=PARENT)
    history = flow.cog.full_conversation_history[PARENT]
    assert history[: current.transcript_start] == previous
    assert history[current.transcript_start :] == [
        "The current conversation's question.",
        "The current conversation's answer.",
    ]
    assert "previous conversation" not in str(history[current.transcript_start :])


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_gateway", [False, True])
async def test_delete_conversation_threads_requires_verified_bot_owner_and_real_guild_context(
    conversation, monkeypatch, missing_gateway
):
    from tests.test_conversation_recovery import thread_payload

    flow = conversation
    owners = [flow.bot.user.id, flow.bot.user.id, MEMBER, None, flow.bot.user.id]
    names = [
        "A conversation with GPT",
        "Closed-GPT",
        "A conversation with GPT",
        "Closed-GPT",
        "Ordinary notes",
    ]
    identifiers = list(range(1750000000000010000, 1750000000000010005))
    for identifier, owner, name in zip(identifiers, owners, names):
        data = thread_payload(identifier, owner=owner or MEMBER)
        data["name"] = name
        thread = discord.Thread(guild=flow.guild, state=flow.state, data=data)
        thread.owner_id = owner
        flow.guild._threads[identifier] = thread
    deletion = AsyncMock()
    monkeypatch.setattr(flow.state.http, "delete_channel", deletion)
    if missing_gateway:
        flow.state._guilds.clear()
    ctx = end_context(flow, PARENT)
    adapter = TextAdapter(flow, PARENT)
    token = async_context.set(adapter)
    try:
        await flow.cog.delete_all_conversation_threads_command(ctx)
    finally:
        async_context.reset(token)
    assert adapter.initial[ctx.interaction.id] == {"type": 5, "data": {"flags": 64}}
    assert adapter.followups[-1]["flags"] & 64
    if missing_gateway:
        deletion.assert_not_awaited()
        assert "No threads were deleted" in adapter.followups[-1]["content"]
    else:
        assert [call.args[0] for call in deletion.await_args_list] == identifiers[:2]
        assert "Deleted 2" in adapter.followups[-1]["content"]
    assert all(identifier in flow.guild._threads for identifier in identifiers[2:])
    assert not flow.provider_calls


@pytest.mark.asyncio
async def test_usage_reports_recorded_cost_without_deriving_fake_token_counts(
    conversation,
):
    flow = conversation
    flow.usage.get_usage = AsyncMock(return_value=0.012345)
    await flow.cog.usage_command(flow.ctx)
    flow.usage.get_usage.assert_awaited_once()
    response = flow.ctx.respond.await_args
    embed = response.kwargs["embed"]
    assert [(field.name, field.value) for field in embed.fields] == [
        ("Recorded cost", "$0.012345")
    ]
    assert "token" not in str(embed.to_dict()).lower()
    assert response.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_transcript_preserves_unanswered_and_followup_entries_without_guessing_speaker_roles(
    conversation,
):
    flow = conversation
    channel = await flow.start()
    thread = flow.guild.get_thread(channel)
    response = discord.Message(
        state=flow.state,
        channel=thread,
        data=message_payload(1650000000000000000, channel, "Download", bot=True),
    )
    flow.messages[response.id] = message_payload(
        response.id, channel, "Download", bot=True
    )
    history = [
        "An unanswered question.",
        "A later question.",
        "An answer to the later question.",
    ]
    button = flow.module.ShareButton(channel, MEMBER, history)
    adapter = TextAdapter(flow, channel)
    interaction = component_interaction(flow, response)
    token = async_context.set(adapter)
    try:
        await button.callback(interaction)
    finally:
        async_context.reset(token)
    assert adapter.uploads[0][1].decode("utf-8") == "\n\n".join(history)
    assert adapter.initial[interaction.id] == {"type": 5, "data": {"flags": 64}}
