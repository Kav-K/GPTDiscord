"""Personal-key setup must respect Discord's one initial response rule."""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest


class StrictResponse:
    def __init__(self):
        self.initial_response = None
        self.modal = None

    def is_done(self):
        return self.initial_response is not None

    def _acknowledge(self, kind):
        if self.is_done():
            raise AssertionError("An interaction can receive only one initial response")
        self.initial_response = kind

    async def defer(self, **kwargs):
        self._acknowledge("defer")

    async def send_message(self, *args, **kwargs):
        self._acknowledge("message")

    async def send_modal(self, modal):
        self._acknowledge("modal")
        self.modal = modal


def interaction_context():
    response = StrictResponse()

    async def followup(*args, **kwargs):
        assert response.is_done(), "Followups require an initial response"

    interaction = SimpleNamespace(
        _state=None,
        response=response,
        followup=SimpleNamespace(send=AsyncMock(side_effect=followup)),
        user=SimpleNamespace(id=42),
    )
    return discord.ApplicationContext(bot=None, interaction=interaction)


@pytest.fixture
def text_service(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALLOWED_GUILDS", "123456789012345678")
    return importlib.import_module("services.text_service")


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_key", [None, ""])
async def test_missing_personal_key_opens_modal_as_the_only_initial_response(
    text_service, stored_key
):
    ctx = interaction_context()
    database = {} if stored_key is None else {42: stored_key}

    key = await text_service.TextService.get_user_api_key(42, ctx, database)

    assert key is None
    assert ctx.response.initial_response == "modal"
    assert isinstance(ctx.response.modal, text_service.SetupModal)
    assert ctx.response.modal.USER_KEY_DB is database
    ctx.followup.send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("acknowledgement", ["defer", "send_message"])
async def test_acknowledged_interaction_gets_private_setup_guidance(
    text_service, acknowledgement
):
    ctx = interaction_context()
    await getattr(ctx.response, acknowledgement)()

    key = await text_service.TextService.get_user_api_key(42, ctx, {})

    assert key is None
    assert ctx.response.modal is None
    ctx.followup.send.assert_awaited_once()
    assert "/setup" in ctx.followup.send.call_args.args[0]
    assert ctx.followup.send.call_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("deferred", [False, True])
async def test_saved_personal_key_does_not_respond_or_select_another_user_key(
    text_service, deferred
):
    ctx = interaction_context()
    if deferred:
        await ctx.response.defer()
    database = {42: "personal-key", 43: "other-user-key"}

    key = await text_service.TextService.get_user_api_key(42, ctx, database)

    assert key == "personal-key"
    assert ctx.response.modal is None
    assert ctx.response.is_done() is deferred
    ctx.followup.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_key_in_conversation_replies_with_setup_guidance(text_service):
    message = SimpleNamespace(reply=AsyncMock())

    key = await text_service.TextService.get_user_api_key(42, message, {})

    assert key is None
    message.reply.assert_awaited_once()
    assert "/setup" in message.reply.call_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command", ["transcribe_file_command", "transcribe_link_command"]
)
async def test_transcription_missing_key_opens_modal_before_deferring(
    text_service, monkeypatch, command
):
    module = importlib.import_module("cogs.transcription_service_cog")
    monkeypatch.setattr(module, "USER_INPUT_API_KEYS", True)
    monkeypatch.setattr(module, "USER_KEY_DB", {})
    model = SimpleNamespace(send_transcription_request=AsyncMock())
    cog = SimpleNamespace(model=model)
    ctx = interaction_context()
    argument = (
        SimpleNamespace(filename="audio.mp3", content_type="audio/mpeg")
        if command == "transcribe_file_command"
        else "https://example.com/audio.mp3"
    )

    await getattr(module.TranscribeService, command)(cog, ctx, argument, 0)

    assert ctx.response.initial_response == "modal"
    assert isinstance(ctx.response.modal, text_service.SetupModal)
    ctx.followup.send.assert_not_awaited()
    model.send_transcription_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_transcription_saved_key_defers_once_and_uses_only_personal_key(
    text_service, monkeypatch
):
    module = importlib.import_module("cogs.transcription_service_cog")
    monkeypatch.setattr(module, "USER_INPUT_API_KEYS", True)
    monkeypatch.setattr(module, "USER_KEY_DB", {42: "personal-key"})
    model = SimpleNamespace(
        send_transcription_request=AsyncMock(return_value="Transcript")
    )
    cog = SimpleNamespace(model=model)
    ctx = interaction_context()
    result_message = SimpleNamespace(edit=AsyncMock())

    async def respond(*args, **kwargs):
        assert ctx.response.initial_response == "defer"
        return result_message

    ctx.interaction.respond = AsyncMock(side_effect=respond)
    attachment = SimpleNamespace(filename="audio.mp3", content_type="audio/mpeg")

    await module.TranscribeService.transcribe_file_command(cog, ctx, attachment, 0)

    assert ctx.response.initial_response == "defer"
    assert ctx.response.modal is None
    model.send_transcription_request.assert_awaited_once_with(
        attachment, 0, "personal-key"
    )
    result_message.edit.assert_awaited_once()
