import discord

from services.environment_service import EnvService
from services.text_service import TextService
from models.index_model import Index_handler

USER_INPUT_API_KEYS = EnvService.get_user_input_api_keys()
USER_KEY_DB = EnvService.get_api_db()

class IndexService(discord.Cog, name="IndexService"):
    """Cog containing gpt-index commands"""
    def __init__(
        self,
        bot,
    ):
        super().__init__()
        self.bot = bot
        self.index_handler = Index_handler(bot)
    
    async def set_index_command(self, ctx, file: discord.Attachment = None, link: str = None):
        """Command handler to set a file as your personal index"""
        if not file and not link:
            await ctx.respond("Please provide a file or a link")
            return

        if file and link:
            await ctx.respond("Please provide only one file or link. Only one or the other.")
            return

        user_api_key = None
        if USER_INPUT_API_KEYS:
            user_api_key = await TextService.get_user_api_key(ctx.user.id, ctx, USER_KEY_DB)
            if not user_api_key:
                return

        await ctx.defer(ephemeral=True)
        if file:
            await self.index_handler.set_file_index(ctx, file, user_api_key=user_api_key)
        elif link:
            await self.index_handler.set_link_index(ctx, link, user_api_key=user_api_key)


    async def set_discord_command(self, ctx, channel: discord.TextChannel = None):
        """Command handler to set a channel as your personal index"""

        user_api_key = None
        if USER_INPUT_API_KEYS:
            user_api_key = await TextService.get_user_api_key(ctx.user.id, ctx, USER_KEY_DB)
            if not user_api_key:
                return

        await ctx.defer(ephemeral=True)
        await self.index_handler.set_discord_index(ctx, channel, user_api_key=user_api_key)

    async def discord_backup_command(self, ctx):
        """Command handler to backup the entire server"""

        user_api_key = None
        if USER_INPUT_API_KEYS:
            user_api_key = await TextService.get_user_api_key(ctx.user.id, ctx, USER_KEY_DB)
            if not user_api_key:
                return

        await ctx.defer(ephemeral=True)
        await self.index_handler.backup_discord(ctx, user_api_key=user_api_key)


    async def load_index_command(self, ctx, index):
        """Command handler to backup the entire server"""
        user_api_key = None
        if USER_INPUT_API_KEYS:
            user_api_key = await TextService.get_user_api_key(ctx.user.id, ctx, USER_KEY_DB)
            if not user_api_key:
                return

        await ctx.defer(ephemeral=True)
        await self.index_handler.load_index(ctx, index, user_api_key)


    async def query_command(self, ctx, query, response_mode):
        """Command handler to query your index"""
        user_api_key = None
        if USER_INPUT_API_KEYS:
            user_api_key = await TextService.get_user_api_key(ctx.user.id, ctx, USER_KEY_DB)
            if not user_api_key:
                return

        await ctx.defer()
        await self.index_handler.query(ctx, query, response_mode, user_api_key)
