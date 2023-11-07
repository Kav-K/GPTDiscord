import discord
from pycord.multicog import add_to_group

from services.environment_service import EnvService
from models.check_model import Check
from models.autocomplete_model import (
    Settings_autocompleter,
    File_autocompleter,
    Translations_autocompleter,
)

ALLOWED_GUILDS = EnvService.get_allowed_guilds()


class Commands(discord.Cog, name="Commands"):
    """Cog containing all slash and context commands as one-liners"""

    def __init__(
        self,
        bot,
        usage_service,
        model,
        message_queue,
        deletion_queue,
        converser_cog,
        image_draw_cog,
        image_service_cog,
        moderations_cog,
        index_cog,
        translations_cog=None,
        search_cog=None,
        transcribe_cog=None,
        code_interpreter_cog=None,
    ):
        super().__init__()
        self.bot = bot
        self.usage_service = usage_service
        self.model = model
        self.message_queue = message_queue
        self.deletion_queue = deletion_queue
        self.converser_cog = converser_cog
        self.image_draw_cog = image_draw_cog
        self.image_service_cog = image_service_cog
        self.moderations_cog = moderations_cog
        self.index_cog = index_cog
        self.translations_cog = translations_cog
        self.search_cog = search_cog
        self.transcribe_cog = transcribe_cog
        self.code_interpreter_cog = code_interpreter_cog

    # Create slash command groups
    dalle = discord.SlashCommandGroup(
        name="dalle",
        description="Dalle related commands",
        guild_ids=ALLOWED_GUILDS,
        checks=[Check.check_dalle_roles()],
    )
    gpt = discord.SlashCommandGroup(
        name="gpt",
        description="GPT related commands",
        guild_ids=ALLOWED_GUILDS,
        checks=[Check.check_gpt_roles()],
    )
    system = discord.SlashCommandGroup(
        name="system",
        description="Admin/System settings for the bot",
        guild_ids=ALLOWED_GUILDS,
        checks=[Check.check_admin_roles()],
    )
    mod = discord.SlashCommandGroup(
        name="mod",
        description="AI-Moderation commands for the bot",
        guild_ids=ALLOWED_GUILDS,
        checks=[Check.check_admin_roles()],
    )
    index = discord.SlashCommandGroup(
        name="index",
        description="Custom index commands for the bot",
        guild_ids=ALLOWED_GUILDS,
        checks=[Check.check_index_roles()],
    )
    transcribe = discord.SlashCommandGroup(
        name="transcribe",
        description="Transcription services using OpenAI Whisper",
        guild_ids=ALLOWED_GUILDS,
        checks=[Check.check_index_roles()],  # TODO new role checker for transcribe
    )
    internet = discord.SlashCommandGroup(
        name="internet",
        description="Transcription services using OpenAI Whisper2",
        guild_ids=ALLOWED_GUILDS,
        checks=[Check.check_index_roles()],  # TODO new role checker for internet
    )
    code_interpreter = discord.SlashCommandGroup(
        name="code",
        description="Code interpreter functionalities",
        guild_ids=ALLOWED_GUILDS,
        checks=[
            Check.check_index_roles()
        ],  # TODO new role checker for code interpreter
    )

    #
    # System commands
    #

    @add_to_group("system")
    @discord.slash_command(
        name="settings",
        description="Get settings for GPT3Discord",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.option(
        name="parameter",
        description="The setting to change",
        required=False,
        autocomplete=Settings_autocompleter.get_settings,
    )
    @discord.option(
        name="value",
        description="The value to set the setting to",
        required=False,
        autocomplete=Settings_autocompleter.get_value,
    )
    @discord.guild_only()
    async def settings(
        self, ctx: discord.ApplicationContext, parameter: str = None, value: str = None
    ):
        await self.converser_cog.settings_command(ctx, parameter, value)

    @add_to_group("system")
    @discord.slash_command(
        name="settings-reset",
        description="Reset all settings for GPT3Discord",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.guild_only()
    async def settings_reset(self, ctx: discord.ApplicationContext):
        await self.converser_cog.settings_reset_command(ctx)

    @add_to_group("system")
    @discord.slash_command(
        name="local-size",
        description="Get the size of the dall-e images folder that we have on the current system",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.guild_only()
    async def local_size(self, ctx: discord.ApplicationContext):
        await self.image_draw_cog.local_size_command(ctx)

    @add_to_group("system")
    @discord.slash_command(
        name="clear-local",
        description="Clear the local dalleimages folder on system.",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.guild_only()
    async def clear_local(self, ctx: discord.ApplicationContext):
        await self.image_draw_cog.clear_local_command(ctx)

    @add_to_group("system")
    @discord.slash_command(
        name="usage",
        description="Get usage statistics for GPT3Discord",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.guild_only()
    async def usage(self, ctx: discord.ApplicationContext):
        await self.converser_cog.usage_command(ctx)

    @add_to_group("system")
    @discord.slash_command(
        name="set-usage",
        description="Set the current OpenAI usage (in dollars)",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.option(
        name="usage_amount",
        description="The current usage amount in dollars and cents (e.g 10.24)",
        type=float,
    )
    async def set_usage(self, ctx: discord.ApplicationContext, usage_amount: float):
        await self.converser_cog.set_usage_command(ctx, usage_amount)

    @add_to_group("system")
    @discord.slash_command(
        name="delete-conversation-threads",
        description="Delete all conversation threads across the bot servers.",
        guild_ids=ALLOWED_GUILDS,
    )
    async def delete_all_conversation_threads(self, ctx: discord.ApplicationContext):
        await self.converser_cog.delete_all_conversation_threads_command(ctx)

    # """
    # Moderation commands
    # """

    @add_to_group("mod")
    @discord.slash_command(
        name="test",
        description="Used to test a prompt and see what threshold values are returned by the moderations endpoint",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.option(
        name="prompt",
        description="The prompt to test",
        required=True,
    )
    @discord.guild_only()
    async def moderations_test(self, ctx: discord.ApplicationContext, prompt: str):
        await self.moderations_cog.moderations_test_command(ctx, prompt)

    @add_to_group("mod")
    @discord.slash_command(
        name="set",
        description="Turn the moderations service on and off",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.option(
        name="status",
        description="Enable or disable the moderations service for the current guild (on/off)",
        required=True,
        choices=["on", "off"],
    )
    @discord.option(
        name="alert_channel_id",
        description="The channel ID to send moderation alerts to",
        required=False,
        autocomplete=Settings_autocompleter.get_value_alert_id_channel,
    )
    @discord.guild_only()
    async def moderations(
        self, ctx: discord.ApplicationContext, status: str, alert_channel_id: str
    ):
        await self.moderations_cog.moderations_command(ctx, status, alert_channel_id)

    @add_to_group("mod")
    @discord.slash_command(
        name="config",
        description="Configure the moderations service for the current guild. Lower # = more strict",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.option(
        name="type",
        description="The type of moderation to configure",
        required=True,
        autocomplete=Settings_autocompleter.get_value_moderations,
    )
    @discord.option(
        name="hate",
        description="The threshold for hate speech",
        required=False,
        min_value=0,
        max_value=1,
    )
    @discord.option(
        name="hate_threatening",
        description="The threshold for hate/threatening speech",
        required=False,
        min_value=0,
        max_value=1,
    )
    @discord.option(
        name="self_harm",
        description="The threshold for self_harm speech",
        required=False,
        min_value=0,
        max_value=1,
    )
    @discord.option(
        name="sexual",
        description="The threshold for sexual speech",
        required=False,
        min_value=0,
        max_value=1,
    )
    @discord.option(
        name="sexual_minors",
        description="The threshold for sexual speech with minors in context",
        required=False,
        min_value=0,
        max_value=1,
    )
    @discord.option(
        name="violence",
        description="The threshold for violent speech",
        required=False,
        min_value=0,
        max_value=1,
    )
    @discord.option(
        name="violence_graphic",
        description="The threshold for violent and graphic speech",
        required=False,
        min_value=0,
        max_value=1,
    )
    @discord.guild_only()
    async def config(
        self,
        ctx: discord.ApplicationContext,
        type: str,
        hate: float,
        hate_threatening: float,
        self_harm: float,
        sexual: float,
        sexual_minors: float,
        violence: float,
        violence_graphic: float,
    ):
        await self.moderations_cog.config_command(
            ctx,
            type,
            hate,
            hate_threatening,
            self_harm,
            sexual,
            sexual_minors,
            violence,
            violence_graphic,
        )

    #
    # GPT commands
    #

    @add_to_group("gpt")
    @discord.slash_command(
        name="instruction",
        description="Set your own system instruction",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.option(
        name="mode",
        description="Set/Get/Clear prompt",
        choices=["set", "get", "clear"],
        required=True,
    )
    @discord.option(
        name="type",
        description="Enable for channel or for user",
        choices=["user", "channel"],
        required=True,
    )
    @discord.option(
        name="instruction", description="The instruction to set", required=False
    )
    @discord.option(
        name="instruction_file",
        description="The instruction to set from a txt file",
        input_type=discord.SlashCommandOptionType.attachment,
        required=False,
    )
    @discord.option(
        name="private", description="Will only be visible to you", required=False
    )
    @discord.guild_only()
    async def instruction(
        self,
        ctx: discord.ApplicationContext,
        mode: str,
        type: str,
        instruction: str,
        instruction_file: discord.Attachment,
        private: bool,
    ):
        await self.converser_cog.instruction_command(
            ctx, mode, type, instruction, instruction_file, private
        )

    @add_to_group("gpt")
    @discord.slash_command(
        name="ask",
        description="Ask the bot something!",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.option(
        name="prompt", description="The prompt to send to the model", required=False
    )
    @discord.option(
        name="prompt_file",
        description="The prompt file to send to the model. Is added before the prompt, both can be combined",
        required=False,
        input_type=discord.SlashCommandOptionType.attachment,
    )
    @discord.option(
        name="model",
        description="The model to use for the request",
        required=False,
        autocomplete=Settings_autocompleter.get_models,
    )
    @discord.option(
        name="private", description="Will only be visible to you", required=False
    )
    @discord.option(
        name="temperature",
        description="Higher values means the model will take more risks",
        required=False,
        min_value=0,
        max_value=2,
    )
    @discord.option(
        name="top_p",
        description="1 is greedy sampling, 0.1 means only considering the top 10% of probability distribution",
        required=False,
        min_value=0,
        max_value=1,
    )
    @discord.option(
        name="frequency_penalty",
        description="Decreasing the model's likelihood to repeat the same line verbatim",
        required=False,
        min_value=-2,
        max_value=2,
    )
    @discord.option(
        name="presence_penalty",
        description="Increasing the model's likelihood to talk about new topics",
        required=False,
        min_value=-2,
        max_value=2,
    )
    @discord.guild_only()
    async def ask(
        self,
        ctx: discord.ApplicationContext,
        prompt: str,
        prompt_file: discord.Attachment,
        model: str,
        private: bool,
        temperature: float,
        top_p: float,
        frequency_penalty: float,
        presence_penalty: float,
    ):
        await self.converser_cog.ask_command(
            ctx,
            prompt,
            private,
            temperature,
            top_p,
            frequency_penalty,
            presence_penalty,
            prompt_file=prompt_file,
            model=model,
        )

    @add_to_group("gpt")
    @discord.slash_command(
        name="edit",
        description="Ask the bot to edit some text!",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.option(
        name="instruction",
        description="How you want the bot to edit the text",
        required=True,
    )
    @discord.option(
        name="text",
        description="The text you want to edit, can be empty",
        required=False,
        default="",
    )
    @discord.option(
        name="private", description="Will only be visible to you", required=False
    )
    @discord.option(
        name="temperature",
        description="Higher values means the model will take more risks",
        required=False,
        input_type=float,
        min_value=0,
        max_value=2,
    )
    @discord.option(
        name="top_p",
        description="1 is greedy sampling, 0.1 means only considering the top 10% of probability distribution",
        required=False,
        input_type=float,
        min_value=0,
        max_value=1,
    )
    @discord.guild_only()
    async def edit(
        self,
        ctx: discord.ApplicationContext,
        instruction: str,
        text: str,
        private: bool,
        temperature: float,
        top_p: float,
    ):
        await self.converser_cog.edit_command(
            ctx, instruction, text, private, temperature, top_p
        )

    @add_to_group("gpt")
    @discord.slash_command(
        name="converse",
        description="Have a conversation with GPT",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.option(
        name="opener",
        description="Which sentence to start with, added after the file",
        required=False,
    )
    @discord.option(
        name="opener_file",
        description="Which file to start with, added before the opener, sets minimal starter",
        required=False,
        autocomplete=File_autocompleter.get_openers,
    )
    @discord.option(
        name="private",
        description="Converse in a private thread",
        required=False,
        default=False,
    )
    @discord.option(
        name="minimal",
        description="Use minimal starter text, saves tokens and has a more open personality",
        required=False,
        default=False,
    )
    @discord.option(
        name="model",
        description="Which model to use with the bot",
        required=False,
        default=False,
        autocomplete=Settings_autocompleter.get_converse_models,
    )
    @discord.option(
        name="temperature",
        description="Higher values means the model will take more risks",
        required=False,
        input_type=float,
        min_value=0,
        max_value=2,
    )
    @discord.option(
        name="top_p",
        description="1 is greedy sampling, 0.1 means only top 10%",
        required=False,
        input_type=float,
        min_value=0,
        max_value=1,
    )
    @discord.option(
        name="frequency_penalty",
        description="Decreasing the model's likelihood to repeat the same line verbatim",
        required=False,
        input_type=float,
        min_value=-2,
        max_value=2,
    )
    @discord.option(
        name="presence_penalty",
        description="Increasing the model's likelihood to talk about new topics",
        required=False,
        input_type=float,
        min_value=-2,
        max_value=2,
    )
    @discord.option(
        name="use_threads",
        description="Set this to false to start a channel conversation",
        required=False,
        default=True,
    )
    @discord.guild_only()
    async def converse(
        self,
        ctx: discord.ApplicationContext,
        opener: str,
        opener_file: str,
        private: bool,
        minimal: bool,
        model: str,
        temperature: float,
        top_p: float,
        frequency_penalty: float,
        presence_penalty: float,
        use_threads: bool,
    ):
        await self.converser_cog.converse_command(
            ctx,
            opener,
            opener_file,
            private,
            minimal,
            model,
            temperature,
            top_p,
            frequency_penalty,
            presence_penalty,
            use_threads=use_threads,
        )

    @add_to_group("gpt")
    @discord.slash_command(
        name="end",
        description="End a conversation with GPT",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.guild_only()
    async def end(self, ctx: discord.ApplicationContext):
        await self.converser_cog.end_command(ctx)

    #
    # Index commands
    #
    @add_to_group("index")
    @discord.slash_command(
        name="rename-user",
        description="Select one of your saved indexes to rename",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.guild_only()
    @discord.option(
        name="user_index",
        description="Which user index to rename",
        required=True,
        autocomplete=File_autocompleter.get_user_indexes,
    )
    @discord.option(
        name="new_name",
        description="The new name",
        required=True,
        type=discord.SlashCommandOptionType.string,
    )
    async def rename_user_index(
        self,
        ctx: discord.ApplicationContext,
        user_index: str,
        new_name: str,
    ):
        await ctx.defer()
        await self.index_cog.rename_user_index_command(ctx, user_index, new_name)

    @add_to_group("index")
    @discord.slash_command(
        name="rename-server",
        description="Select one of your saved server indexes to rename",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.guild_only()
    @discord.option(
        name="server_index",
        description="Which server index to rename",
        required=True,
        autocomplete=File_autocompleter.get_server_indexes,
    )
    @discord.option(
        name="new_name",
        description="The new name",
        required=True,
        type=discord.SlashCommandOptionType.string,
    )
    async def rename_server_index(
        self,
        ctx: discord.ApplicationContext,
        server_index: str,
        new_name: str,
    ):
        await ctx.defer()
        await self.index_cog.rename_server_index_command(ctx, server_index, new_name)

    @add_to_group("index")
    @discord.slash_command(
        name="rename-search",
        description="Select one of your saved search indexes to rename",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.guild_only()
    @discord.option(
        name="search_index",
        description="Which search index to rename",
        required=True,
        autocomplete=File_autocompleter.get_user_search_indexes,
    )
    @discord.option(
        name="new_name",
        description="The new name",
        required=True,
        type=discord.SlashCommandOptionType.string,
    )
    async def rename_search_index(
        self,
        ctx: discord.ApplicationContext,
        search_index: str,
        new_name: str,
    ):
        await ctx.defer()
        await self.index_cog.rename_search_index_command(ctx, search_index, new_name)

    @add_to_group("index")
    @discord.slash_command(
        name="load",
        description="Select one of your saved indexes to query from",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.guild_only()
    @discord.option(
        name="user_index",
        description="Which user file to load the index from",
        required=False,
        autocomplete=File_autocompleter.get_user_indexes,
    )
    @discord.option(
        name="server_index",
        description="Which server file to load the index from",
        required=False,
        autocomplete=File_autocompleter.get_server_indexes,
    )
    @discord.option(
        name="search_index",
        description="Which search index file to load the index from",
        required=False,
        autocomplete=File_autocompleter.get_user_search_indexes,
    )
    async def load_index(
        self,
        ctx: discord.ApplicationContext,
        user_index: str,
        server_index: str,
        search_index: str,
    ):
        await ctx.defer()
        await self.index_cog.load_index_command(
            ctx, user_index, server_index, search_index
        )

    @add_to_group("index")
    @discord.slash_command(
        name="chat",
        description="Select one of your saved indexes to talk to",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.guild_only()
    @discord.option(
        name="model",
        description="The model to use for the conversation",
        required=False,
        default="gpt-4-32k",
        autocomplete=Settings_autocompleter.get_index_and_search_models,
    )
    async def talk(
        self,
        ctx: discord.ApplicationContext,
        model: str,
    ):
        await ctx.defer()
        await self.index_cog.index_chat_command(ctx, model)

    @add_to_group("index")
    @discord.slash_command(
        name="add", description="Add an index to query from", guild_ids=ALLOWED_GUILDS
    )
    @discord.guild_only()
    @discord.option(
        name="file",
        description="A file to create the index from",
        required=False,
        input_type=discord.SlashCommandOptionType.attachment,
    )
    @discord.option(
        name="link",
        description="A link to a file to a webpage ",
        required=False,
        input_type=str,
    )
    async def set_file(
        self, ctx: discord.ApplicationContext, file: discord.Attachment, link: str
    ):
        await self.index_cog.set_index_command(ctx, file, link)

    @add_to_group("index")
    @discord.slash_command(
        name="recurse-link",
        description="Recursively index a link",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.guild_only()
    @discord.option(
        name="link",
        description="A link to create the index from",
        required=True,
        input_type=discord.SlashCommandOptionType.string,
    )
    @discord.option(
        name="depth",
        description="How deep to recurse",
        required=False,
        input_type=discord.SlashCommandOptionType.integer,
        min_value=1,
        max_value=5,
    )
    async def set_recurse_link(
        self, ctx: discord.ApplicationContext, link: str, depth: int
    ):
        await self.index_cog.set_index_link_recurse_command(ctx, link, depth)

    @add_to_group("index")
    @discord.slash_command(
        name="reset",
        description="Reset (delete) all of your saved indexes",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.guild_only()
    async def reset(self, ctx: discord.ApplicationContext):
        await self.index_cog.reset_command(ctx)

    @add_to_group("index")
    @discord.slash_command(
        name="compose",
        description="Combine multiple indexes together",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.option(
        name="name",
        description="The name of the new index",
        required=False,
        input_type=discord.SlashCommandOptionType.string,
    )
    @discord.guild_only()
    async def compose(self, ctx: discord.ApplicationContext, name: str):
        await self.index_cog.compose_command(ctx, name)

    @add_to_group("index")
    @discord.slash_command(
        name="add_discord",
        description="Set a index from a discord channel",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.guild_only()
    @discord.option(
        name="channel",
        description="A channel to create the index from",
        required=False,
        input_type=discord.SlashCommandOptionType.channel,
    )
    @discord.option(
        name="message_limit",
        description="The number of messages to index",
        required=False,
        input_type=discord.SlashCommandOptionType.integer,
    )
    async def set_discord(
        self,
        ctx: discord.ApplicationContext,
        channel: discord.TextChannel,
        message_limit: int,
    ):
        await self.index_cog.set_discord_command(
            ctx, channel, message_limit=message_limit
        )

    @add_to_group("index")
    @discord.slash_command(
        name="discord_backup",
        description="Save an index made from the whole server",
        guild_ids=ALLOWED_GUILDS,
        checks=[Check.check_admin_roles(), Check.check_index_roles()],
    )
    @discord.option(
        name="message_limit",
        description="The number of messages to index per channel",
        required=False,
        input_type=discord.SlashCommandOptionType.integer,
    )
    @discord.guild_only()
    async def discord_backup(self, ctx: discord.ApplicationContext, message_limit: int):
        await self.index_cog.discord_backup_command(ctx, message_limit=message_limit)

    @add_to_group("index")
    @discord.slash_command(
        name="query", description="Query from your index", guild_ids=ALLOWED_GUILDS
    )
    @discord.guild_only()
    @discord.option(name="query", description="What to query the index", required=True)
    @discord.option(
        name="nodes",
        description="How many nodes should the response be queried from, only non-deep indexes",
        required=False,
        default=1,
        min_value=1,
        max_value=5,
        input_type=discord.SlashCommandOptionType.integer,
    )
    @discord.option(
        name="response_mode",
        description="Response mode, doesn't work on deep composed indexes",
        guild_ids=ALLOWED_GUILDS,
        required=False,
        default="refine",
        choices=["refine", "compact", "tree_summarize"],
    )
    @discord.option(
        name="child_branch_factor",
        description="Only for deep indexes, how deep to go, higher is expensive.",
        required=False,
        default=1,
        min_value=1,
        max_value=3,
        input_type=discord.SlashCommandOptionType.integer,
    )
    @discord.option(
        name="model",
        description="The model to use for the request (querying, not composition)",
        required=False,
        default="gpt-4-32k",
        autocomplete=Settings_autocompleter.get_index_and_search_models,
    )
    @discord.option(
        name="multistep",
        description="Do a more intensive, multi-step query,",
        required=False,
        default=False,
        input_type=discord.SlashCommandOptionType.boolean,
    )
    async def query(
        self,
        ctx: discord.ApplicationContext,
        query: str,
        nodes: int,
        response_mode: str,
        child_branch_factor: int,
        model: str,
        multistep: bool,
    ):
        await ctx.defer()
        await self.index_cog.query_command(
            ctx,
            query,
            nodes,
            response_mode,
            child_branch_factor,
            model,
            multistep,
        )

    #
    # DALLE commands
    #

    @add_to_group("dalle")
    @discord.slash_command(
        name="draw_old",
        description="Draw an image from a prompt using the old DALLE-2 Model",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.option(name="prompt", description="The prompt to draw from", required=True)
    async def draw_old(self, ctx: discord.ApplicationContext, prompt: str):
        await self.image_draw_cog.draw_old_command(ctx, prompt)

    @add_to_group("dalle")
    @discord.slash_command(
        name="draw",
        description="Draw an image from a prompt using the new DALLE-3 Model. Does not support Variations.",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.option(name="prompt", description="The prompt to draw from", required=True)
    @discord.option(
        name="quality",
        description="Image quality",
        required=False,
        default="hd",
        autocomplete=Settings_autocompleter.get_dalle3_image_qualities,
    )
    @discord.option(
        name="image_size",
        description="How big you want the generated image to be",
        required=False,
        default="1024x1024",
        autocomplete=Settings_autocompleter.get_dalle3_image_sizes,
    )
    @discord.option(
        name="style",
        description="The style of the generated images, choose between realism/vivid",
        required=False,
        default="natural",
        autocomplete=Settings_autocompleter.get_dalle3_image_styles,
    )
    async def draw(
        self,
        ctx: discord.ApplicationContext,
        prompt: str,
        quality: str,
        image_size: str,
        style: str,
    ):
        await self.image_draw_cog.draw_command(ctx, prompt, quality, image_size, style)

    @add_to_group("dalle")
    @discord.slash_command(
        name="optimize",
        description="Optimize a text prompt for DALL-E/MJ/SD image generation.",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.option(
        name="prompt", description="The text prompt to optimize.", required=True
    )
    @discord.guild_only()
    async def optimize(self, ctx: discord.ApplicationContext, prompt: str):
        await self.image_service_cog.optimize_command(ctx, prompt)

    #
    # Other commands
    #

    @discord.slash_command(
        name="private-test",
        description="Private thread for testing. Only visible to you and server admins.",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.guild_only()
    async def private_test(self, ctx: discord.ApplicationContext):
        await self.converser_cog.private_test_command(ctx)

    @discord.slash_command(
        name="help", description="Get help for GPT3Discord", guild_ids=ALLOWED_GUILDS
    )
    @discord.guild_only()
    async def help(self, ctx: discord.ApplicationContext):
        await self.converser_cog.help_command(ctx)

    @discord.slash_command(
        name="setup",
        description="Setup your API key for use with GPT3Discord",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.guild_only()
    async def setup(self, ctx: discord.ApplicationContext):
        await self.converser_cog.setup_command(ctx)

    #
    # Text-based context menu commands from here
    #

    @discord.message_command(
        name="Ask GPT", guild_ids=ALLOWED_GUILDS, checks=[Check.check_gpt_roles()]
    )
    async def ask_gpt_action(self, ctx, message: discord.Message):
        await self.converser_cog.ask_gpt_action(ctx, message)

    #
    # Image-based context menu commands from here
    #

    @discord.message_command(
        name="Draw", guild_ids=ALLOWED_GUILDS, checks=[Check.check_dalle_roles()]
    )
    async def draw_action(self, ctx, message: discord.Message):
        await self.image_draw_cog.draw_action(ctx, message)

    """
    Code interpreter commands and actions
    """

    @add_to_group("code")
    @discord.slash_command(
        name="chat",
        description="Chat with code-interpreting GPT!",
        guild_ids=ALLOWED_GUILDS,
        checks=[Check.check_search_roles()],
    )
    @discord.option(
        name="model",
        description="The model to use for the request (querying, not composition)",
        required=False,
        default="gpt-4-32k",
        autocomplete=Settings_autocompleter.get_index_and_search_models,
    )
    async def chat_code(
        self,
        ctx: discord.ApplicationContext,
        model: str,
    ):
        if not self.code_interpreter_cog:
            await ctx.respond(
                "Code interpretation is disabled on this server.", ephemeral=True
            )
            return
        await self.code_interpreter_cog.code_interpreter_chat_command(ctx, model=model)

    """
    Translation commands and actions
    """

    @discord.slash_command(
        name="translate",
        description="Translate text to a given language",
        guild_ids=ALLOWED_GUILDS,
        checks=[Check.check_translator_roles()],
    )
    @discord.option(name="text", description="The text to translate", required=True)
    @discord.option(
        name="target_language",
        description="The language to translate to",
        required=True,
        autocomplete=Translations_autocompleter.get_languages,
    )
    @discord.option(
        name="formality",
        description="Formal/Informal tone of translation",
        required=False,
        autocomplete=Translations_autocompleter.get_formality_values,
    )
    @discord.guild_only()
    async def translate(
        self,
        ctx: discord.ApplicationContext,
        text: str,
        target_language: str,
        formality: str,
    ):
        if self.translations_cog:
            await self.translations_cog.translate_command(
                ctx, text, target_language, formality
            )
        else:
            await ctx.respond(
                "Translations are disabled on this server.", ephemeral=True
            )

    @discord.slash_command(
        name="languages",
        description="View the supported languages for translation",
        guild_ids=ALLOWED_GUILDS,
        checks=[Check.check_translator_roles()],
    )
    @discord.guild_only()
    async def languages(self, ctx: discord.ApplicationContext):
        if self.translations_cog:
            await self.translations_cog.languages_command(ctx)
        else:
            await ctx.respond(
                "Translations are disabled on this server.", ephemeral=True
            )

    @discord.message_command(
        name="Translate",
        guild_ids=ALLOWED_GUILDS,
        checks=[Check.check_translator_roles()],
    )
    async def translate_action(self, ctx, message: discord.Message):
        if self.translations_cog:
            await self.translations_cog.translate_action(ctx, message)
        else:
            await ctx.respond(
                "Translations are disabled on this server.", ephemeral=True
            )

    # @discord.message_command(
    #     name="Paraphrase",
    #     guild_ids=ALLOWED_GUILDS,
    #     checks=[Check.check_gpt_roles()],
    # )
    # async def paraphrase_action(self, ctx, message: discord.Message):
    #     await self.converser_cog.paraphrase_action(ctx, message)

    @discord.message_command(
        name="Elaborate",
        guild_ids=ALLOWED_GUILDS,
        checks=[Check.check_gpt_roles()],
    )
    async def elaborate_action(self, ctx, message: discord.Message):
        await self.converser_cog.elaborate_action(ctx, message)

    @discord.message_command(
        name="Summarize",
        guild_ids=ALLOWED_GUILDS,
        checks=[Check.check_gpt_roles()],
    )
    async def summarize_action(self, ctx, message: discord.Message):
        await self.converser_cog.summarize_action(ctx, message)

    @add_to_group("internet")
    @discord.slash_command(
        name="chat",
        description="Chat with GPT connected to the internet!",
        guild_ids=ALLOWED_GUILDS,
        checks=[Check.check_search_roles()],
    )
    @discord.option(
        name="search_scope",
        description="How many top links to use for context",
        required=False,
        input_type=discord.SlashCommandOptionType.integer,
        max_value=6,
        min_value=1,
        default=2,
    )
    @discord.option(
        name="model",
        description="The model to use for the request (querying, not composition)",
        required=False,
        default="gpt-4-32k",
        autocomplete=Settings_autocompleter.get_index_and_search_models,
    )
    async def chat(
        self,
        ctx: discord.ApplicationContext,
        model: str,
        search_scope: int = 2,
    ):
        await self.search_cog.search_chat_command(
            ctx, search_scope=search_scope, model=model
        )

    # Search slash commands
    @add_to_group("internet")
    @discord.slash_command(
        name="search",
        description="Search google alongside GPT for something",
        guild_ids=ALLOWED_GUILDS,
        checks=[Check.check_search_roles()],
    )
    @discord.option(name="query", description="The query to search", required=True)
    @discord.option(
        name="scope",
        description="How many top links to use for context",
        required=False,
        input_type=discord.SlashCommandOptionType.integer,
        max_value=16,
        min_value=1,
    )
    @discord.option(
        name="nodes",
        description="The higher the number, the more accurate the results, but more expensive",
        required=False,
        input_type=discord.SlashCommandOptionType.integer,
        max_value=8,
        min_value=1,
    )
    @discord.option(
        name="deep",
        description="Do a more intensive, long-running search",
        required=False,
        input_type=discord.SlashCommandOptionType.boolean,
    )
    @discord.option(
        name="response_mode",
        description="Response mode, doesn't work on deep searches",
        guild_ids=ALLOWED_GUILDS,
        required=False,
        default="refine",
        choices=["refine", "compact", "tree_summarize"],
    )
    @discord.option(
        name="model",
        description="The model to use for the request (querying, not composition)",
        required=False,
        default="gpt-4-32k",
        autocomplete=Settings_autocompleter.get_index_and_search_models,
    )
    @discord.option(
        name="multistep",
        description="Do a more intensive, multi-step query,",
        required=False,
        default=False,
        input_type=discord.SlashCommandOptionType.boolean,
    )
    @discord.guild_only()
    async def search(
        self,
        ctx: discord.ApplicationContext,
        query: str,
        scope: int,
        nodes: int,
        deep: bool,
        response_mode: str,
        model: str,
        multistep: bool,
    ):
        await self.search_cog.search_command(
            ctx,
            query,
            scope,
            nodes,
            deep,
            response_mode,
            model,
            multistep,
        )

    # Transcribe commands
    @add_to_group("transcribe")
    @discord.slash_command(
        name="file",
        description="Transcribe an audio or video file",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.guild_only()
    @discord.option(
        name="file",
        description="A file to transcribe",
        required=True,
        input_type=discord.SlashCommandOptionType.attachment,
    )
    @discord.option(
        name="temperature",
        description="The higher the value, the riskier the model will be",
        required=False,
        input_type=discord.SlashCommandOptionType.number,
        max_value=1,
        min_value=0,
    )
    async def transcribe_file(
        self,
        ctx: discord.ApplicationContext,
        file: discord.Attachment,
        temperature: float,
    ):
        await self.transcribe_cog.transcribe_file_command(ctx, file, temperature)

    @add_to_group("transcribe")
    @discord.slash_command(
        name="link",
        description="Transcribe a file link or youtube link",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.guild_only()
    @discord.option(
        name="link",
        description="A link to transcribe",
        required=True,
        input_type=discord.SlashCommandOptionType.string,
    )
    @discord.option(
        name="temperature",
        description="The higher the value, the riskier the model will be",
        required=False,
        input_type=discord.SlashCommandOptionType.number,
        max_value=1,
        min_value=0,
    )
    async def transcribe_link(
        self, ctx: discord.ApplicationContext, link: str, temperature: float
    ):
        await self.transcribe_cog.transcribe_link_command(ctx, link, temperature)
