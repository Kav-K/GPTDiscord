"""Slow prompt work must follow API-key setup and Discord acknowledgement."""

import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from tests.test_user_api_key import StrictResponse


@pytest.fixture
def prompt_flow(monkeypatch, tmp_path):
    monkeypatch.setenv("ALLOWED_GUILDS", "11")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    module = importlib.import_module("cogs.text_service_cog")
    monkeypatch.setattr(module, "USER_INPUT_API_KEYS", True)
    monkeypatch.setattr(module, "PRE_MODERATE", False)
    events = []
    elapsed = [0]

    class DeadlineResponse(StrictResponse):
        def _acknowledge(self, kind):
            assert elapsed[0] < 3, "Initial response exceeded Discord's deadline"
            super()._acknowledge(kind)
            events.append(kind)

    response = DeadlineResponse()

    async def followup(*args, **kwargs):
        assert response.is_done()
        events.append("followup")

    async def respond(*args, **kwargs):
        if response.is_done():
            return await followup(*args, **kwargs)
        return await response.send_message(*args, **kwargs)

    interaction = SimpleNamespace(
        _state=None,
        response=response,
        followup=SimpleNamespace(send=AsyncMock(side_effect=followup)),
        respond=AsyncMock(side_effect=respond),
        user=SimpleNamespace(id=42),
    )
    ctx = discord.ApplicationContext(None, interaction)

    async def download(file):
        assert response.initial_response == "defer"
        events.append("download")
        elapsed[0] += 4
        await asyncio.sleep(0)
        return b"File prompt mentioning <@123>."

    async def mention(_, prompt):
        assert response.initial_response == "defer"
        events.append("mention")
        elapsed[0] += 4
        await asyncio.sleep(0)
        return prompt.replace("<@123>", "Member")

    def count_tokens(prompt):
        assert response.initial_response == "defer"
        events.append("tokens")
        return 10

    async def provider(*args, **kwargs):
        assert response.initial_response == "defer"
        events.append("provider")

    class KeyDB(dict):
        def get(self, key, default=None):
            assert not response.is_done()
            events.append("key_lookup")
            return super().get(key, default)

    database = KeyDB()
    monkeypatch.setattr(module, "USER_KEY_DB", database)
    download_mock = AsyncMock(side_effect=download)
    provider_mock = AsyncMock(side_effect=provider)
    monkeypatch.setattr(module, "read_attachment_bytes", download_mock)
    monkeypatch.setattr(module.TextService, "encapsulated_send", provider_mock)
    cog = module.GPT3ComCon.__new__(module.GPT3ComCon)
    cog.model = SimpleNamespace(
        prompt_min_length=6,
        max_tokens=4000,
        usage_service=SimpleNamespace(count_tokens=Mock(side_effect=count_tokens)),
    )
    cog.mention_to_username = AsyncMock(side_effect=mention)
    cog.instructions = {}
    return SimpleNamespace(
        module=module,
        cog=cog,
        ctx=ctx,
        database=database,
        events=events,
        download=download_mock,
        provider=provider_mock,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("saved", [False, True])
async def test_prompt_file_waits_for_key_setup_and_initial_response(prompt_flow, saved):
    if saved:
        prompt_flow.database[42] = "personal-key"
    file = object()

    await prompt_flow.cog.ask_command(prompt_flow.ctx, "Extra prompt", prompt_file=file)

    if saved:
        assert prompt_flow.events == [
            "key_lookup",
            "defer",
            "download",
            "mention",
            "provider",
        ]
        prompt_flow.download.assert_awaited_once_with(file)
        assert (
            prompt_flow.provider.call_args.args[2]
            == "File prompt mentioning Member.\n\nExtra prompt"
        )
        assert prompt_flow.provider.call_args.kwargs["custom_api_key"] == "personal-key"
    else:
        assert prompt_flow.events == ["key_lookup", "modal"]
        prompt_flow.download.assert_not_awaited()
        prompt_flow.cog.mention_to_username.assert_not_awaited()
        prompt_flow.provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_host_key_prompt_file_is_acknowledged_before_download(
    prompt_flow, monkeypatch
):
    monkeypatch.setattr(prompt_flow.module, "USER_INPUT_API_KEYS", False)

    await prompt_flow.cog.ask_command(prompt_flow.ctx, None, prompt_file=object())

    assert prompt_flow.events == ["defer", "download", "mention", "provider"]
    assert prompt_flow.provider.call_args.kwargs["custom_api_key"] is None


@pytest.mark.asyncio
async def test_missing_prompt_is_rejected_before_setup_or_attachment_work(prompt_flow):
    await prompt_flow.cog.ask_command(prompt_flow.ctx, None)

    assert prompt_flow.events == ["message"]
    prompt_flow.download.assert_not_awaited()
    prompt_flow.provider.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("saved", [False, True])
async def test_edit_waits_for_key_setup_and_acknowledgement_before_mentions(
    prompt_flow, saved
):
    if saved:
        prompt_flow.database[42] = "personal-key"

    await prompt_flow.cog.edit_command(
        prompt_flow.ctx, "Improve <@123>", "Text <@123>", False, None, None
    )

    if saved:
        assert prompt_flow.events == [
            "key_lookup",
            "defer",
            "mention",
            "mention",
            "provider",
        ]
        assert prompt_flow.provider.call_args.kwargs["instruction"] == "Improve Member"
        assert prompt_flow.provider.call_args.kwargs["custom_api_key"] == "personal-key"
    else:
        assert prompt_flow.events == ["key_lookup", "modal"]
        prompt_flow.cog.mention_to_username.assert_not_awaited()
        prompt_flow.provider.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("saved", [False, True])
@pytest.mark.parametrize(
    "action, prefix",
    [
        ("ask_gpt_action", "Message Member"),
        ("paraphrase_action", "Paraphrase the following text"),
        ("elaborate_action", "Elaborate with more information"),
        ("summarize_action", "Summarize the following message"),
    ],
)
async def test_context_menus_do_not_resolve_mentions_or_count_tokens_before_setup(
    prompt_flow, saved, action, prefix
):
    if saved:
        prompt_flow.database[42] = "personal-key"
    message = SimpleNamespace(
        content="Message <@123>", jump_url="https://discord.com/test"
    )

    await getattr(prompt_flow.cog, action)(prompt_flow.ctx, message)

    if saved:
        assert prompt_flow.events[:3] == ["key_lookup", "defer", "mention"]
        assert prompt_flow.events[-1] == "provider"
        assert prompt_flow.provider.call_args.args[2].startswith(prefix)
        assert prompt_flow.provider.call_args.kwargs["custom_api_key"] == "personal-key"
    else:
        assert prompt_flow.events == ["key_lookup", "modal"]
        prompt_flow.cog.model.usage_service.count_tokens.assert_not_called()
        prompt_flow.cog.mention_to_username.assert_not_awaited()
        prompt_flow.provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_instruction_file_is_already_deferred_before_download(prompt_flow):
    await prompt_flow.cog.instruction_command(
        prompt_flow.ctx, "set", "user", None, object(), True
    )

    assert prompt_flow.events == ["defer", "download", "followup"]
    assert prompt_flow.cog.instructions[42].prompt == "File prompt mentioning <@123>."
    prompt_flow.provider.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action", ["paraphrase_action", "elaborate_action", "summarize_action"]
)
async def test_context_menu_length_limit_still_rejects_after_acknowledgement(
    prompt_flow, action
):
    prompt_flow.database[42] = "personal-key"
    prompt_flow.cog.model.usage_service.count_tokens.side_effect = lambda _: 5000
    message = SimpleNamespace(
        content="Message <@123>", jump_url="https://discord.com/test"
    )

    await getattr(prompt_flow.cog, action)(prompt_flow.ctx, message)

    assert prompt_flow.events == ["key_lookup", "defer", "mention", "followup"]
    prompt_flow.provider.assert_not_awaited()
