import traceback

import discord

from services.environment_service import EnvService
from services.moderations_service import Moderation
from services.text_service import TextService
from models.index_model import Index_handler

USER_INPUT_API_KEYS = EnvService.get_user_input_api_keys()
USER_KEY_DB = EnvService.get_api_db()
PRE_MODERATE = EnvService.get_premoderate()


class IndexService(discord.Cog, name="IndexService"):
    """Cog containing gpt-index commands"""

    def __init__(
        self,
        bot,
        usage_service,
    ):
        super().__init__()
        self.bot = bot
        self.index_handler = Index_handler(bot, usage_service)

    async def set_index_command(
        self, ctx, file: discord.Attachment = None, link: str = None
    ):
        await ctx.defer()
        """Command handler to set a file as your personal index"""
        if not file and not link:
            await ctx.respond("Please provide a file or a link")
            return

        if file and link:
            await ctx.respond(
                "Please provide only one file or link. Only one or the other."
            )
            return

        user_api_key = None
        if USER_INPUT_API_KEYS:
            user_api_key = await TextService.get_user_api_key(
                ctx.user.id, ctx, USER_KEY_DB
            )
            if not user_api_key:
                return

        if file:
            await self.index_handler.set_file_index(
                ctx, file, user_api_key=user_api_key
            )
        elif link:
            await self.index_handler.set_link_index(
                ctx, link, user_api_key=user_api_key
            )

    async def set_discord_command(self, ctx, channel: discord.TextChannel = None):
        """Command handler to set a channel as your personal index"""
        await ctx.defer()

        user_api_key = None
        if USER_INPUT_API_KEYS:
            user_api_key = await TextService.get_user_api_key(
                ctx.user.id, ctx, USER_KEY_DB
            )
            if not user_api_key:
                return

        await self.index_handler.set_discord_index(
            ctx, channel, user_api_key=user_api_key
        )

    async def reset_command(self, ctx):
        await ctx.defer()
        try:
            self.index_handler.reset_indexes(ctx.user.id)
            await ctx.respond("Your indexes have been reset")
        except:
            traceback.print_exc()
            await ctx.respond(
                "Something went wrong while resetting your indexes. Contact the server admin."
            )

    async def discord_backup_command(self, ctx):
        """Command handler to backup the entire server"""
        await ctx.defer()

        user_api_key = None
        if USER_INPUT_API_KEYS:
            user_api_key = await TextService.get_user_api_key(
                ctx.user.id, ctx, USER_KEY_DB
            )
            if not user_api_key:
                return
        await self.index_handler.backup_discord(ctx, user_api_key=user_api_key)

    async def load_index_command(self, ctx, user_index, server_index, search_index):
        """Command handler to load indexes"""

        if not user_index and not server_index and not search_index:
            await ctx.respond("Please provide a user or server or search index")
            return

        if (
            user_index
            and server_index
            or user_index
            and search_index
            or server_index
            and search_index
        ):
            await ctx.respond(
                "Please only try to load one type of index. Either a user index, a server index or a search index."
            )
            return

        search = False
        if server_index:
            index = server_index
            server = True
        elif user_index:
            index = user_index
            server = False
        else:
            index = search_index
            server = False
            search = True

        user_api_key = None
        if USER_INPUT_API_KEYS:
            user_api_key = await TextService.get_user_api_key(
                ctx.user.id, ctx, USER_KEY_DB
            )
            if not user_api_key:
                return
        await self.index_handler.load_index(ctx, index, server, search, user_api_key)

    async def query_command(self, ctx, query, nodes, response_mode):
        """Command handler to query your index"""

        user_api_key = None
        if USER_INPUT_API_KEYS:
            user_api_key = await TextService.get_user_api_key(
                ctx.user.id, ctx, USER_KEY_DB
            )
            if not user_api_key:
                return

        # Check the opener for bad content.
        if PRE_MODERATE:
            if await Moderation.simple_moderate_and_respond(query, ctx):
                return

        await self.index_handler.query(ctx, query, response_mode, nodes, user_api_key)

    async def compose_command(self, ctx, name):
        """Command handler to compose from your index"""
        user_api_key = None
        if USER_INPUT_API_KEYS:
            user_api_key = await TextService.get_user_api_key(
                ctx.user.id, ctx, USER_KEY_DB
            )
            if not user_api_key:
                return

        await self.index_handler.compose(ctx, name, user_api_key)
