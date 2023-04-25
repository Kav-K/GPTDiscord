import datetime
import traceback
from pathlib import Path

import discord
import os

from models.embed_statics_model import EmbedStatics
from services.deletion_service import Deletion
from services.environment_service import EnvService
from services.moderations_service import Moderation
from services.text_service import TextService
from models.index_model import Index_handler

USER_INPUT_API_KEYS = EnvService.get_user_input_api_keys()
USER_KEY_DB = EnvService.get_api_db()
PRE_MODERATE = EnvService.get_premoderate()
GITHUB_TOKEN = EnvService.get_github_token()
if GITHUB_TOKEN:
    os.environ["GITHUB_TOKEN"] = GITHUB_TOKEN


class IndexService(discord.Cog, name="IndexService"):
    """Cog containing gpt-index commands"""

    def __init__(
        self,
        bot,
        usage_service,
        deletion_queue,
    ):
        super().__init__()
        self.bot = bot
        self.index_handler = Index_handler(bot, usage_service)
        self.thread_awaiting_responses = []
        self.deletion_queue = deletion_queue

    @discord.Cog.listener()
    async def on_message(self, message):
        # Check for self
        if message.author == self.bot.user:
            return

        # Check if the message is from a guild.
        if not message.guild:
            return

        if message.content.strip().startswith("~"):
            return

        if message.channel.id in self.thread_awaiting_responses:
            resp_message = await message.reply(
                "Please wait for the agent to respond to a previous message first!"
            )
            deletion_time = datetime.datetime.now() + datetime.timedelta(seconds=5)
            deletion_time = deletion_time.timestamp()

            original_deletion_message = Deletion(message, deletion_time)
            deletion_message = Deletion(resp_message, deletion_time)
            await self.deletion_queue.put(deletion_message)
            await self.deletion_queue.put(original_deletion_message)
            return

        # Pre moderation
        if PRE_MODERATE:
            if await Moderation.simple_moderate_and_respond(message.content, message):
                await message.delete()
                return

        prompt = message.content.strip()

        self.thread_awaiting_responses.append(message.channel.id)

        try:
            await message.channel.trigger_typing()
        except:
            pass

        chat_result = await self.index_handler.execute_index_chat_message(
            message, prompt
        )
        if chat_result:
            await message.channel.send(chat_result)
            self.thread_awaiting_responses.remove(message.channel.id)

    async def index_chat_command(self, ctx, user_index, search_index, model):
        if not user_index and not search_index:
            await ctx.respond("Please provide a valid user index or search index")
            return

        await self.index_handler.start_index_chat(ctx, search_index, user_index, model)

        pass

    async def rename_user_index_command(self, ctx, user_index, new_name):
        """Command handler to rename a user index"""

        if not new_name:
            await ctx.respond(
                await EmbedStatics.get_index_rename_failure_embed(
                    user_index.split("/")[-1],
                    "None",
                    "Please provide a new name for this index",
                )
            )
            return

        if await self.index_handler.rename_index(
            ctx,
            f"indexes/{ctx.user.id}/{user_index}",
            f"indexes/{ctx.user.id}/{new_name}",
        ):
            await ctx.respond(
                embed=EmbedStatics.get_index_rename_success_embed(
                    user_index.split("/")[-1], new_name
                )
            )
        else:
            await ctx.respond(
                embed=EmbedStatics.get_index_rename_failure_embed(
                    user_index.split("/")[-1],
                    new_name,
                    "Please check the server console for more details.",
                )
            )

    async def rename_server_index_command(self, ctx, server_index, new_name):
        """Command handler to rename a user index"""

        if not new_name:
            await ctx.respond(
                await EmbedStatics.get_index_rename_failure_embed(
                    server_index.split("/")[-1],
                    "None",
                    "Please provide a new name for this index",
                )
            )
            return

        if await self.index_handler.rename_index(
            ctx,
            f"indexes/{ctx.guild.id}/{server_index}",
            f"indexes/{ctx.guild.id}/{new_name}",
        ):
            await ctx.respond(
                embed=EmbedStatics.get_index_rename_success_embed(
                    server_index.split("/")[-1], new_name
                )
            )
        else:
            await ctx.respond(
                embed=EmbedStatics.get_index_rename_failure_embed(
                    server_index.split("/")[-1],
                    new_name,
                    "Please check the server console for more details.",
                )
            )

    async def rename_search_index_command(self, ctx, search_index, new_name):
        if not new_name:
            await ctx.respond(
                await EmbedStatics.get_index_rename_failure_embed(
                    search_index.split("/")[-1],
                    "None",
                    "Please provide a new name for this index",
                )
            )
            return

        if await self.index_handler.rename_index(
            ctx,
            f"indexes/{ctx.user.id}_search/{search_index}",
            f"indexes/{ctx.user.id}_search/{new_name}",
        ):
            await ctx.respond(
                embed=EmbedStatics.get_index_rename_success_embed(
                    search_index.split("/")[-1], new_name
                )
            )
        else:
            await ctx.respond(
                embed=EmbedStatics.get_index_rename_failure_embed(
                    search_index.split("/")[-1],
                    new_name,
                    "Please check the server console for more details.",
                )
            )

    async def set_index_link_recurse_command(
        self, ctx, link: str = None, depth: int = 1
    ):
        await ctx.defer()
        """Command handler to set a file as your personal index"""
        if not link:
            await ctx.respond("Please provide a link")
            return

        user_api_key = None
        if USER_INPUT_API_KEYS:
            user_api_key = await TextService.get_user_api_key(
                ctx.user.id, ctx, USER_KEY_DB
            )
            if not user_api_key:
                return

        await self.index_handler.set_link_index_recurse(
            ctx, link, depth, user_api_key=user_api_key
        )

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

    async def set_discord_command(
        self, ctx, channel: discord.TextChannel = None, message_limit: int = 2500
    ):
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
            ctx, channel, user_api_key=user_api_key, message_limit=message_limit
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

    async def discord_backup_command(self, ctx, message_limit: int = 2500):
        """Command handler to backup the entire server"""
        await ctx.defer()

        user_api_key = None
        if USER_INPUT_API_KEYS:
            user_api_key = await TextService.get_user_api_key(
                ctx.user.id, ctx, USER_KEY_DB
            )
            if not user_api_key:
                return
        await self.index_handler.backup_discord(
            ctx, user_api_key=user_api_key, message_limit=message_limit
        )

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

    async def query_command(
        self,
        ctx,
        query,
        nodes,
        response_mode,
        child_branch_factor,
        model,
        multistep,
    ):
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

        await self.index_handler.query(
            ctx,
            query,
            response_mode,
            nodes,
            user_api_key,
            child_branch_factor,
            model,
            multistep,
        )

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
