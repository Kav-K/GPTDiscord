import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.fixture
def commands_module(monkeypatch, tmp_path):
    monkeypatch.setenv("ALLOWED_GUILDS", "123456789012345678")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr("tiktoken.get_encoding", Mock())
    return importlib.import_module("cogs.commands")


@pytest.mark.asyncio
async def test_command_groups_register_with_current_multicog(commands_module):
    import discord
    from pycord.multicog import Bot

    bot = Bot(intents=discord.Intents.none())
    commands = commands_module.Commands(*([None] * 11))
    bot.add_cog(commands)

    groups = {command.name: command for command in bot.pending_application_commands}
    assert {"gpt", "dalle", "index", "internet", "transcribe"} <= groups.keys()
    assert "converse" in {command.name for command in groups["gpt"].subcommands}
    assert "draw" in {command.name for command in groups["dalle"].subcommands}
    await bot.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command_name, args, expected_setup",
    [
        ("set_file", [None, None], ".[index]"),
        ("search", ["question"], "/web"),
        ("transcribe_file", [None, None], "OpenAI credentials"),
        ("chat_code", [None], "E2B_API_KEY"),
        ("languages", [], "DEEPL_TOKEN"),
    ],
)
async def test_unavailable_commands_report_setup_help(
    commands_module, command_name, args, expected_setup
):
    commands = commands_module.Commands(*([None] * 11))
    ctx = SimpleNamespace(respond=AsyncMock())

    await getattr(commands_module.Commands, command_name).callback(commands, ctx, *args)

    ctx.respond.assert_awaited_once()
    assert expected_setup in ctx.respond.call_args.args[0]
    assert ctx.respond.call_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_model_autocomplete_uses_current_catalog_and_filters_prefix(
    commands_module,
):
    from models.autocomplete_model import Settings_autocompleter, _matching_choices
    from models.openai_model import Models

    choices = await Settings_autocompleter.get_models(SimpleNamespace(value=""))
    assert choices[0] == Models.DEFAULT
    assert "gpt-4-vision-preview" not in choices
    assert "gpt-4-32k" not in choices
    assert len(choices) <= 25
    assert await Settings_autocompleter.get_function_calling_models(
        SimpleNamespace(value="GPT-4.1")
    ) == [model for model in Models.TEXT_MODELS if model.startswith("gpt-4.1")]
    assert len(_matching_choices(SimpleNamespace(value=""), map(str, range(30)))) == 25
