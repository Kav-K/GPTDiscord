"""Help describes the actual SDK command manifest without repeating feature policy."""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from pycord.multicog import Bot
import pytest


@pytest.fixture
def help_module(monkeypatch, tmp_path):
    monkeypatch.setenv("ALLOWED_GUILDS", "123456789012345678")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SHARE_DIR", str(tmp_path))
    monkeypatch.setenv("USER_INPUT_API_KEYS", "false")
    monkeypatch.delenv("GOOGLE_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_SEARCH_ENGINE_ID", raising=False)
    return importlib.import_module("cogs.text_service_cog")


async def help_fields(module, bot):
    ctx = SimpleNamespace(defer=AsyncMock(), respond=AsyncMock())
    await module.GPT3ComCon.help_command(SimpleNamespace(bot=bot), ctx)
    ctx.defer.assert_awaited_once_with()
    ctx.respond.assert_awaited_once()
    assert ctx.respond.await_args.kwargs["ephemeral"] is False
    return {
        field.name: field.value
        for field in ctx.respond.await_args.kwargs["embed"].fields
    }


def add_commands(bot, *, optional=False, search=False):
    from cogs.commands import Commands
    from cogs.web_search_cog import WebSearchCog

    service = object()
    bot.add_cog(
        Commands(
            bot,
            *([None] * 8),
            index_cog=service if optional else None,
            translations_cog=service if optional else None,
            search_cog=service if optional or search else None,
            transcribe_cog=service,
            code_interpreter_cog=service if optional else None,
        )
    )
    bot.add_cog(WebSearchCog(bot))


@pytest.mark.asyncio
async def test_help_omits_unregistered_optional_groups_but_keeps_base_features(
    help_module,
):
    bot = Bot(intents=discord.Intents.none())
    add_commands(bot)
    try:
        fields = await help_fields(help_module, bot)
        assert {
            "/gpt",
            "/gpt ask",
            "/dalle draw",
            "/system settings",
            "/transcribe",
            "/web",
            "/help",
        } <= fields.keys()
        assert {
            "/index",
            "/index chat",
            "/internet chat",
            "/internet search",
            "/code chat",
            "/translate",
        }.isdisjoint(fields)
        assert (
            fields["/dalle draw"]
            == "Generate images with the configured GPT Image model."
        )
        assert "/system settings <model parameter> <value>" in fields
    finally:
        await bot.close()


@pytest.mark.asyncio
async def test_help_keeps_openai_chat_without_google_search(help_module):
    bot = Bot(intents=discord.Intents.none())
    add_commands(bot, search=True)
    try:
        fields = await help_fields(help_module, bot)
        assert "/internet chat" in fields
        assert "/internet search" not in fields
        assert "/code chat" not in fields
    finally:
        await bot.close()


@pytest.mark.asyncio
async def test_help_includes_loaded_optional_commands_and_configured_google(
    help_module, monkeypatch
):
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "offline-google-key")
    monkeypatch.setenv("GOOGLE_SEARCH_ENGINE_ID", "offline-google-engine")
    bot = Bot(intents=discord.Intents.none())
    add_commands(bot, optional=True)
    try:
        fields = await help_fields(help_module, bot)
        assert {
            "/index",
            "/index chat",
            "/internet chat",
            "/internet search",
            "/code chat",
            "/translate",
        } <= fields.keys()
    finally:
        await bot.close()


@pytest.mark.asyncio
async def test_help_uses_exact_slash_manifest_even_if_credentials_and_context_names_differ(
    help_module, monkeypatch
):
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "offline-google-key")
    monkeypatch.setenv("GOOGLE_SEARCH_ENGINE_ID", "offline-google-engine")
    bot = Bot(intents=discord.Intents.none())
    group = discord.SlashCommandGroup("internet", "Internet tools")

    @group.command(name="chat")
    async def chat(ctx):
        pass

    bot.add_application_command(group)

    @bot.message_command(name="internet search")
    async def context_search(ctx, message):
        pass

    try:
        fields = await help_fields(help_module, bot)
        assert set(fields) == {"/internet chat"}
    finally:
        await bot.close()
