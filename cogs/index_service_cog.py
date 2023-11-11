import datetime
import traceback

import aiofiles
import discord
import os

import openai
from discord.ext import pages

from models.embed_statics_model import EmbedStatics
from services.deletion_service import Deletion
from services.environment_service import EnvService
from services.moderations_service import Moderation
from services.text_service import TextService
from models.index_model import Index_handler
from utils.safe_ctx_respond import safe_remove_list

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

    async def process_indexing(self, message, index_type, content=None, link=None):
        """
        Helper method to process indexing for both files and links.
        - index_type: 'file' or 'link'
        - content: The file content if index_type is 'file'
        - link: The link if index_type is 'link'
        """
        thinking_embed = discord.Embed(
            title=f"🤖💬 Indexing {index_type} and saving to agent knowledge",
            color=0x808080,
        )
        thinking_embed.set_footer(text="This may take a few seconds.")

        try:
            thinking_message = await message.reply(embed=thinking_embed)
        except:
            traceback.print_exc()

        if index_type == "file":
            indexing_result, summary = await self.index_handler.index_chat_file(
                message, content
            )
        else:
            indexing_result, summary = await self.index_handler.index_link(
                link, summarize=True, index_chat_ctx=message
            )
            print("The summary is " + str(summary))

        try:
            await thinking_message.delete()
        except:
            pass

        if not indexing_result:
            failure_embed = discord.Embed(
                title="Indexing Error",
                description=f"Your {index_type} could not be indexed",
                color=discord.Color.red(),
            )
            failure_embed.set_thumbnail(url="https://i.imgur.com/hbdBZfG.png")
            await message.reply(embed=failure_embed)
            safe_remove_list(self.thread_awaiting_responses, message.channel.id)
            return False

        success_embed = discord.Embed(
            title=f"{index_type.capitalize()} Interpreted",
            description=f"The {index_type} you've uploaded has successfully been interpreted. The summary is below:\n`{summary}`",
            color=discord.Color.green(),
        )
        success_embed.set_thumbnail(url="https://i.imgur.com/I5dIdg6.png")
        await message.reply(embed=success_embed)
        return True

    @discord.Cog.listener()
    async def on_message(self, message):
        # Check for self
        if message.author == self.bot.user:
            return

        if message.type != discord.MessageType.default:
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

        if await self.index_handler.get_is_in_index_chat(message):
            self.thread_awaiting_responses.append(message.channel.id)

            try:
                await message.channel.trigger_typing()
            except:
                pass

            # Handle file uploads
            file = message.attachments[0] if len(message.attachments) > 0 else None

            # File operations, allow for user file upload
            if file:
                indexing_result = await self.process_indexing(
                    message, "file", content=file
                )

                if not indexing_result:
                    safe_remove_list(self.thread_awaiting_responses, message.channel.id)
                    return

                prompt += (
                    "\n{System Message: the user has just uploaded the file "
                    + str(file.filename)
                    + "Unless the user asked a specific question, do not use your tools and instead just acknowledge the upload}"
                )

            # Link operations, allow for user link upload, we connect and download the content at the link.
            if "http" in prompt:
                # Extract the entire link
                link = prompt[prompt.find("http") :]

                indexing_result = await self.process_indexing(
                    message, "link", link=link
                )

                if not indexing_result:
                    safe_remove_list(self.thread_awaiting_responses, message.channel.id)
                    return

                prompt += (
                    "\n{System Message: you have just indexed the link "
                    + str(link)
                    + "}"
                )
            try:
                chat_result = await self.index_handler.execute_index_chat_message(
                    message, prompt
                )
            except openai.BadRequestError as e:
                traceback.print_exc()
                await message.reply(
                    "This model is not supported with connected conversations."
                )

            if chat_result:
                if len(chat_result) > 2000:
                    embed_pages = EmbedStatics.paginate_chat_embed(chat_result)

                    for x, page in enumerate(embed_pages):
                        if x == 0:
                            previous_message = await message.reply(embed=page)
                        else:
                            previous_message = previous_message.reply(embed=page)

                else:
                    chat_result = chat_result.replace("\\n", "\n")
                    # Build a response embed
                    response_embed = discord.Embed(
                        title="",
                        description=chat_result,
                        color=0x808080,
                    )
                    await message.reply(
                        embed=response_embed,
                    )
                safe_remove_list(self.thread_awaiting_responses, message.channel.id)

    async def index_chat_command(self, ctx, model):
        await self.index_handler.start_index_chat(ctx, model)

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
