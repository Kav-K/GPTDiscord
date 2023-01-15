import discord
from pycord.multicog import add_to_group

from services.environment_service import EnvService
from models.check_model import Check
from models.autocomplete_model import Settings_autocompleter, File_autocompleter

ALLOWED_GUILDS = EnvService.get_allowed_guilds()


class Commands(discord.Cog, name="Commands"):
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

    """
    System commands
    """

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

    """
    (system) Moderation commands
    """

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
    )
    @discord.option(
        name="alert_channel_id",
        description="The channel ID to send moderation alerts to",
        required=False,
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
        description="The type of moderation to configure ('warn' or 'delete')",
        required=True,
    )
    @discord.option(
        name="hate",
        description="The threshold for hate speech",
        required=False,
        input_type=float,
        min_value=0,
        max_value=1,
    )
    @discord.option(
        name="hate_threatening",
        description="The threshold for hate/threatening speech",
        required=False,
        input_type=float,
        min_value=0,
        max_value=1,
    )
    @discord.option(
        name="self_harm",
        description="The threshold for self_harm speech",
        required=False,
        input_type=float,
        min_value=0,
        max_value=1,
    )
    @discord.option(
        name="sexual",
        description="The threshold for sexual speech",
        required=False,
        input_type=float,
        min_value=0,
        max_value=1,
    )
    @discord.option(
        name="sexual_minors",
        description="The threshold for sexual speech with minors in context",
        required=False,
        input_type=float,
        min_value=0,
        max_value=1,
    )
    @discord.option(
        name="violence",
        description="The threshold for violent speech",
        required=False,
        input_type=float,
        min_value=0,
        max_value=1,
    )
    @discord.option(
        name="violence_graphic",
        description="The threshold for violent and graphic speech",
        required=False,
        input_type=float,
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

    """
    GPT commands
    """

    @add_to_group("gpt")
    @discord.slash_command(
        name="ask",
        description="Ask GPT3 something!",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.option(
        name="prompt", description="The prompt to send to GPT3", required=True
    )
    @discord.option(
        name="temperature",
        description="Higher values means the model will take more risks",
        required=False,
        input_type=float,
        min_value=0,
        max_value=1,
    )
    @discord.option(
        name="top_p",
        description="1 is greedy sampling, 0.1 means only considering the top 10% of probability distribution",
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
    @discord.guild_only()
    async def ask(
        self,
        ctx: discord.ApplicationContext,
        prompt: str,
        temperature: float,
        top_p: float,
        frequency_penalty: float,
        presence_penalty: float,
    ):
        await self.converser_cog.ask_command(
            ctx, prompt, temperature, top_p, frequency_penalty, presence_penalty
        )

    @add_to_group("gpt")
    @discord.slash_command(
        name="edit",
        description="Ask GPT3 to edit some text!",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.option(
        name="instruction",
        description="How you want GPT3 to edit the text",
        required=True,
    )
    @discord.option(
        name="input",
        description="The text you want to edit, can be empty",
        required=False,
        default="",
    )
    @discord.option(
        name="temperature",
        description="Higher values means the model will take more risks",
        required=False,
        input_type=float,
        min_value=0,
        max_value=1,
    )
    @discord.option(
        name="top_p",
        description="1 is greedy sampling, 0.1 means only considering the top 10% of probability distribution",
        required=False,
        input_type=float,
        min_value=0,
        max_value=1,
    )
    @discord.option(
        name="codex", description="Enable codex version", required=False, default=False
    )
    @discord.guild_only()
    async def edit(
        self,
        ctx: discord.ApplicationContext,
        instruction: str,
        input: str,
        temperature: float,
        top_p: float,
        codex: bool,
    ):
        await self.converser_cog.edit_command(
            ctx, instruction, input, temperature, top_p, codex
        )

    @add_to_group("gpt")
    @discord.slash_command(
        name="converse",
        description="Have a conversation with GPT3",
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
    @discord.guild_only()
    async def converse(
        self,
        ctx: discord.ApplicationContext,
        opener: str,
        opener_file: str,
        private: bool,
        minimal: bool,
    ):
        await self.converser_cog.converse_command(
            ctx, opener, opener_file, private, minimal
        )

    @add_to_group("gpt")
    @discord.slash_command(
        name="end",
        description="End a conversation with GPT3",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.guild_only()
    async def end(self, ctx: discord.ApplicationContext):
        await self.converser_cog.end_command(ctx)

    """
    DALLE commands
    """

    @add_to_group("dalle")
    @discord.slash_command(
        name="draw",
        description="Draw an image from a prompt",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.option(name="prompt", description="The prompt to draw from", required=True)
    async def draw(self, ctx: discord.ApplicationContext, prompt: str):
        await self.image_draw_cog.draw_command(ctx, prompt)

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

    """
    Other commands
    """

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

    """
    Text-based context menu commands from here
    """

    @discord.message_command(
        name="Ask GPT", guild_ids=ALLOWED_GUILDS, checks=[Check.check_gpt_roles()]
    )
    async def ask_gpt_action(self, ctx, message: discord.Message):
        await self.converser_cog.ask_gpt_action(ctx, message)

    """
    Image-based context menu commands from here
    """

    @discord.message_command(
        name="Draw", guild_ids=ALLOWED_GUILDS, checks=[Check.check_dalle_roles()]
    )
    async def draw_action(self, ctx, message: discord.Message):
        await self.image_draw_cog.draw_action(ctx, message)
