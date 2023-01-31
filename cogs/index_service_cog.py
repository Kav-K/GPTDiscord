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
        self.index_handler = Index_handler()
    
    async def set_index_command(self, ctx, file: discord.Attachment):
        """Command handler to set a file as your personal index"""

        user_api_key = None
        if USER_INPUT_API_KEYS:
            user_api_key = await TextService.get_user_api_key(ctx.user.id, ctx, USER_KEY_DB)
            if not user_api_key:
                return

        await ctx.defer(ephemeral=True)
        await self.index_handler.set_file_index(ctx, file, user_api_key=user_api_key)


    async def set_discord_command(self, ctx, channel: discord.TextChannel = None):
        """Command handler to set a channel as your personal index"""

        user_api_key = None
        if USER_INPUT_API_KEYS:
            user_api_key = await TextService.get_user_api_key(ctx.user.id, ctx, USER_KEY_DB)
            if not user_api_key:
                return

        await ctx.defer(ephemeral=True)
        if not channel:
            await self.index_handler.set_discord_index(ctx, channel, user_api_key=user_api_key, no_channel=True)
            return
        await self.index_handler.set_discord_index(ctx, channel, user_api_key=user_api_key)


    async def query_command(self, ctx, query):
        """Command handler to query your index"""
        user_api_key = None
        if USER_INPUT_API_KEYS:
            user_api_key = await TextService.get_user_api_key(ctx.user.id, ctx, USER_KEY_DB)
            if not user_api_key:
                return

        await ctx.defer()
        await self.index_handler.query(ctx, query, user_api_key=user_api_key)
