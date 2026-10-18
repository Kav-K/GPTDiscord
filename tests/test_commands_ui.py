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
async def test_command_groups_register_with_current_multicog(commands_module, monkeypatch):
    import discord
    from pycord.multicog import Bot

    bot = Bot(intents=discord.Intents.none())
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "offline-google-key")
    monkeypatch.setenv("GOOGLE_SEARCH_ENGINE_ID", "offline-engine")
    commands = commands_module.Commands(
        *([None] * 9), index_cog=object(), search_cog=object(),
        transcribe_cog=object(),
    )
    bot.add_cog(commands)

    groups = {command.name: command for command in bot.pending_application_commands}
    assert {"gpt", "dalle", "index", "internet", "transcribe"} <= groups.keys()
    assert "converse" in {command.name for command in groups["gpt"].subcommands}
    assert {"draw", "optimize"} == {
        command.name for command in groups["dalle"].subcommands
    }
    chat = next(command for command in groups["internet"].subcommands if command.name == "chat")
    provider = next(option for option in chat.options if option.name == "provider")
    assert provider.default == "openai"
    assert {choice.value for choice in provider.choices} == {"openai", "google"}
    await bot.close()


@pytest.mark.asyncio
async def test_unloaded_integrations_have_no_registered_slash_or_context_commands(
    commands_module, monkeypatch,
):
    import discord
    from pycord.multicog import Bot

    monkeypatch.setenv("USER_INPUT_API_KEYS", "false")
    bot = Bot(intents=discord.Intents.none())
    try:
        bot.add_cog(commands_module.Commands(*([None] * 11)))
        assert {command.name for command in bot.pending_application_commands} == {
            "gpt", "dalle", "system", "mod", "private-test", "help",
            "Ask GPT", "Draw", "Elaborate", "Summarize",
        }
    finally:
        await bot.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("key,engine", [(False, False), (True, False), (False, True), (True, True)])
async def test_google_registration_keeps_openai_chat_and_its_bound_handler(
    commands_module, monkeypatch, key, engine,
):
    import discord
    from pycord.multicog import Bot

    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "offline-key" if key else "")
    monkeypatch.setenv("GOOGLE_SEARCH_ENGINE_ID", "offline-engine" if engine else "")
    search = SimpleNamespace(search_chat_command=AsyncMock())
    commands = commands_module.Commands(*([None] * 11), search_cog=search)
    bot = Bot(intents=discord.Intents.none())
    try:
        bot.add_cog(commands)
        group = next(command for command in bot.pending_application_commands if command.name == "internet")
        assert {command.name for command in group.subcommands} == (
            {"chat", "search"} if key and engine else {"chat"}
        )
        chat = next(command for command in group.subcommands if command.name == "chat")
        provider = next(option for option in chat.options if option.name == "provider")
        assert {choice.value for choice in provider.choices} == (
            {"openai", "google"} if key and engine else {"openai"}
        )
        assert provider.default == "openai"
        assert chat.parent is group and chat.cog is commands
        ctx = object()
        await chat.callback(chat.cog, ctx, "gpt-6.1-sol")
        search.search_chat_command.assert_awaited_once_with(
            ctx, search_scope=2, model="gpt-6.1-sol", temperature=0, top_p=1, provider="openai",
        )
    finally:
        await bot.close()


@pytest.mark.asyncio
async def test_availability_is_isolated_across_enabled_and_disabled_instances(
    commands_module, monkeypatch,
):
    import discord
    from pycord.multicog import Bot

    bots = []
    snapshots = []
    try:
        for enabled in (True, False, True):
            monkeypatch.setenv("USER_INPUT_API_KEYS", str(enabled).lower())
            monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "offline-key" if enabled else "")
            monkeypatch.setenv("GOOGLE_SEARCH_ENGINE_ID", "offline-engine" if enabled else "")
            service = object() if enabled else None
            commands = commands_module.Commands(
                *([None] * 9), index_cog=service, translations_cog=service,
                search_cog=service, transcribe_cog=service, code_interpreter_cog=service,
            )
            bot = Bot(intents=discord.Intents.none())
            bots.append(bot)
            bot.add_cog(commands)
            groups = {command.name: command for command in bot.pending_application_commands}
            optional = {"index", "internet", "code", "transcribe", "translate", "languages", "Translate", "setup"}
            assert optional.intersection(groups) == (optional if enabled else set())
            snapshots.append([command.to_dict() for command in bot.pending_application_commands])
        assert snapshots[0] == snapshots[2]
        assert [command.to_dict() for command in bots[0].pending_application_commands] == snapshots[0]
        for bot in bots:
            owner = bot.get_cog("Commands")
            for command in bot.pending_application_commands:
                assert command.cog is owner
                if isinstance(command, discord.SlashCommandGroup):
                    assert all(child.cog is owner and child.parent is command for child in command.subcommands)
                    assert len({child.name for child in command.subcommands}) == len(command.subcommands)
    finally:
        for bot in bots:
            await bot.close()


@pytest.mark.asyncio
async def test_image_options_use_current_catalog_and_keep_prompt_styles(
    commands_module,
):
    from models.autocomplete_model import Settings_autocompleter
    from models.openai_model import ImageSize

    ctx = SimpleNamespace(value="", options={"parameter": "image_size"})
    assert (
        await Settings_autocompleter.get_value(ctx)
        == ImageSize.ALL_SIZES
        == ["auto", "1024x1024", "1536x1024", "1024x1536"]
    )
    assert await Settings_autocompleter.get_image_sizes(ctx) == ImageSize.ALL_SIZES
    assert await Settings_autocompleter.get_image_qualities(ctx) == [
        "auto",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]
    assert await Settings_autocompleter.get_image_styles(ctx) == ["natural", "vivid"]
    ctx.value = "15"
    assert await Settings_autocompleter.get_image_sizes(ctx) == ["1536x1024"]


@pytest.mark.asyncio
async def test_internet_chat_forwards_provider(commands_module):
    search = SimpleNamespace(search_chat_command=AsyncMock())
    commands = commands_module.Commands(*([None] * 11), search_cog=search)
    ctx = object()
    await commands_module.Commands.chat.callback(
        commands, ctx, "gpt-6.1-sol", provider="google", search_scope=4,
    )
    search.search_chat_command.assert_awaited_once_with(
        ctx, model="gpt-6.1-sol", search_scope=4,
        temperature=0, top_p=1, provider="google",
    )


@pytest.mark.asyncio
async def test_internet_chat_model_suggestions_match_provider(commands_module):
    from cogs.web_search_cog import configured_web_models
    from models.openai_model import Models

    ctx = SimpleNamespace(value="", options={})
    assert await commands_module.internet_chat_models(ctx) == configured_web_models()[:25]
    ctx.options["provider"] = "google"
    assert await commands_module.internet_chat_models(ctx) == Models.TEXT_MODELS[:25]


@pytest.mark.asyncio
async def test_data_chat_exposes_and_forwards_saved_index_selection(commands_module):
    import discord
    from pycord.multicog import Bot

    index = SimpleNamespace(index_chat_command=AsyncMock())
    commands = commands_module.Commands(*([None] * 9), index)
    bot = Bot(intents=discord.Intents.none())
    bot.add_cog(commands)
    try:
        group = next(c for c in bot.pending_application_commands if c.name == "index")
        chat = next(c for c in group.subcommands if c.name == "chat")
        option = next(o for o in chat.options if o.name == "user_index")
        assert option.required is False
        assert option.autocomplete is not None
        ctx = object()
        await commands_module.Commands.talk.callback(
            commands, ctx, "gpt-6.1-sol", user_index="10_18_resume_pdf",
        )
        index.index_chat_command.assert_awaited_once_with(
            ctx, "gpt-6.1-sol", 0, 1, user_index="10_18_resume_pdf",
        )
    finally:
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
