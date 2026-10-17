import asyncio
import datetime
import io
import pickle
import re
import traceback
import sys
from pathlib import Path


import aiofiles
import json

import discord
from discord.ext import pages

from models.deepl_model import TranslationModel
from models.embed_statics_model import EmbedStatics
from models.image_understanding_model import ImageUnderstandingModel
from models.openai_model import Models, Override
from services.environment_service import EnvService
from services.access_service import has_any_role, member_has_access, message_has_access
from services.discord_attachment_service import read_attachment_bytes
from services.conversation_recovery_service import ConversationRecoveryService
from services.message_queue_service import Message
from services.moderations_service import Moderation
from models.user_model import Thread, EmbeddedConversationItem, Instruction
from collections import defaultdict
from sqlitedict import SqliteDict

from services.pickle_service import Pickler
from services.text_service import SetupModal, TextService
from utils.safe_ctx_respond import safe_ctx_respond, safe_remove_list
from utils.model_settings import editable_model_settings

original_message = {}
ALLOWED_GUILDS = EnvService.get_allowed_guilds()
if sys.platform == "win32":
    separator = "\\"
else:
    separator = "/"

#
# Get the user key service if it is enabled.
#
USER_INPUT_API_KEYS = EnvService.get_user_input_api_keys()
USER_KEY_DB = EnvService.get_api_db()
CHAT_BYPASS_ROLES = EnvService.get_bypass_roles()
PRE_MODERATE = EnvService.get_premoderate()
FORCE_ENGLISH = EnvService.get_force_english()
BOT_TAGGABLE = EnvService.get_bot_is_taggable()
CHANNEL_CHAT_ROLES = EnvService.get_channel_chat_roles()
BOT_TAGGABLE_ROLES = EnvService.get_gpt_roles()
CHANNEL_INSTRUCTION_ROLES = EnvService.get_channel_instruction_roles()
image_understanding_model = ImageUnderstandingModel()

#
# Obtain the Moderation table and the General table, these are two SQLite tables that contain
# information about the server that are used for persistence and to auto-restart the moderation service.
#
MOD_DB = None
GENERAL_DB = None
try:
    print("Attempting to retrieve the General and Moderations DB")
    MOD_DB = SqliteDict(
        EnvService.find_shared_file("main_db.sqlite"),
        tablename="moderations",
        autocommit=True,
    )
    GENERAL_DB = SqliteDict(
        EnvService.find_shared_file("main_db.sqlite"),
        tablename="general",
        autocommit=True,
    )
    print("Retrieved the General and Moderations DB")
except Exception as e:
    print("Failed to retrieve the General and Moderations DB. The bot is terminating.")
    raise e

BOT_NAME = EnvService.get_custom_bot_name()


class GPT3ComCon(discord.Cog, name="GPT3ComCon"):
    def __init__(
        self,
        bot,
        usage_service,
        model,
        message_queue,
        deletion_queue,
        DEBUG_GUILD,
        DEBUG_CHANNEL,
        data_path: Path,
        pinecone_service,
        pickle_queue,
    ):
        super().__init__()
        self.GLOBAL_COOLDOWN_TIME = 0.25

        # Environment
        self.data_path = data_path
        self.debug_channel = None

        # Services and models
        self.bot = bot
        self.usage_service = usage_service
        self.model = model
        self.translation_model = TranslationModel()
        self.deletion_queue = deletion_queue

        # Data specific to all text based GPT interactions
        self.users_to_interactions = defaultdict(list)
        self.redo_users = {}

        # Pickle queue
        self.pickle_queue = pickle_queue

        # Conversations-specific data
        self.END_PROMPTS = [
            "end",
            "end conversation",
            "end the conversation",
            "that's all",
            "that'll be all",
        ]
        self.awaiting_responses = []
        self.awaiting_thread_responses = []
        self.conversation_threads = {}
        self.full_conversation_history = defaultdict(list)
        self.instructions = defaultdict(list)
        self.summarize = self.model.summarize_conversations

        # Pinecone data
        self.pinecone_service = pinecone_service

        try:  # TODO Clean this up, this is gross
            conversation_file_path = EnvService.find_shared_file(
                "conversation_starter_pretext.txt"
            )
            # Attempt to read a conversation starter text string from the file.
            with conversation_file_path.open("r", encoding="utf-8") as f:
                self.CONVERSATION_STARTER_TEXT = f.read()
                print(
                    f"Conversation starter text loaded from {conversation_file_path}."
                )
            assert self.CONVERSATION_STARTER_TEXT is not None

            language_detect_file_path = EnvService.find_shared_file(
                "language_detection_pretext.txt"
            )
            # Attempt to read a conversation starter text string from the file.
            with language_detect_file_path.open("r", encoding="utf-8") as f:
                self.LANGUAGE_DETECT_STARTER_TEXT = f.read()
                print(
                    f"Language detection starter text loaded from {language_detect_file_path}."
                )
            assert self.LANGUAGE_DETECT_STARTER_TEXT is not None

            conversation_file_path_minimal = EnvService.find_shared_file(
                "conversation_starter_pretext_minimal.txt"
            )
            with conversation_file_path_minimal.open("r", encoding="utf-8") as f:
                self.CONVERSATION_STARTER_TEXT_MINIMAL = f.read()
                print(
                    f"Conversation starter text loaded from {conversation_file_path_minimal}."
                )
            assert self.CONVERSATION_STARTER_TEXT_MINIMAL is not None

            conversation_file_path_vision = EnvService.find_shared_file(
                "conversation_starter_pretext_vision.txt"
            )
            with conversation_file_path_vision.open("r", encoding="utf-8") as f:
                self.CONVERSATION_STARTER_TEXT_VISION = f.read()
                print(
                    f"Conversation starter text loaded from {conversation_file_path_vision}."
                )
            assert self.CONVERSATION_STARTER_TEXT_VISION is not None

            conversation_drawing_ability_snippet = EnvService.find_shared_file(
                "conversation_drawing_ability_snippet.txt"
            )
            with conversation_drawing_ability_snippet.open("r", encoding="utf-8") as f:
                self.CONVERSATION_DRAWING_ABILITY_SNIPPET = f.read()
                print(
                    f"Conversation starter text loaded from {conversation_drawing_ability_snippet}."
                )
            assert self.CONVERSATION_DRAWING_ABILITY_SNIPPET is not None

            conversation_drawing_ability_extraction_snippet = (
                EnvService.find_shared_file(
                    "conversation_drawing_ability_extraction_snippet.txt"
                )
            )
            with conversation_drawing_ability_extraction_snippet.open(
                "r", encoding="utf-8"
            ) as f:
                self.CONVERSATION_DRAWING_ABILITY_EXTRACTION_SNIPPET = f.read()
                print(
                    f"Conversation starter text loaded from {conversation_drawing_ability_extraction_snippet}."
                )
            assert self.CONVERSATION_DRAWING_ABILITY_EXTRACTION_SNIPPET is not None

        except Exception:
            self.CONVERSATION_STARTER_TEXT = self.CONVERSATION_STARTER_TEXT_MINIMAL = (
                self.CONVERSATION_STARTER_TEXT_VISION
            ) = (
                "You are an artificial intelligence that is able to do anything, and answer any question,"
                "I want you to be my personal assistant and help me with some tasks. "
                "and I want you to make well-informed decisions using the data that you have been trained on, "
                "and be sure to be mindful of the previous conversation history and be consistent with your answers."
            )

        self.DEBUG_GUILD = DEBUG_GUILD
        self.DEBUG_CHANNEL = DEBUG_CHANNEL
        print(
            f"The debug channel and guild IDs are {self.DEBUG_GUILD} and {self.DEBUG_CHANNEL}"
        )
        self.TEXT_CUTOFF = 1900
        self.EMBED_CUTOFF = 3900
        self.message_queue = message_queue
        self.conversation_thread_owners = defaultdict(list)
        self.conversation_recovery = ConversationRecoveryService(self, data_path)

    def cog_unload(self):
        self.bot.loop.create_task(self.conversation_recovery.close())

    async def load_file(self, file, ctx):
        """Take filepath, return content or respond if not found"""
        try:
            async with aiofiles.open(file, "r", encoding="utf-8") as f:
                return await f.read()
        except Exception as e:
            traceback.print_exc()
            await ctx.respond(
                "Error loading file. Please check that it is correctly placed in the bot's root file directory."
            )
            raise e

    @discord.Cog.listener()
    async def on_member_join(self, member):
        """When members join send welcome message if enabled"""
        if not member_has_access(member):
            return
        if self.model.welcome_message_enabled:
            query = f"Please generate a welcome message for {member.name} who has just joined the server."

            try:
                welcome_message_response = await self.model.send_request(
                    query,
                    tokens=self.usage_service.count_tokens(query),
                    is_chatgpt_request=True,
                )
                welcome_message = str(
                    welcome_message_response["choices"][0]["message"]["content"]
                )
            except Exception:
                welcome_message = None

            if not welcome_message:
                welcome_message = EnvService.get_welcome_message()
            welcome_embed = discord.Embed(
                title=f"Welcome, {member.name}!", description=welcome_message
            )

            welcome_embed.add_field(
                name="Just so you know...",
                value="> My commands are invoked with a forward slash (/)\n> Use /help to see my help message(s).",
            )
            await member.send(content=None, embed=welcome_embed)

    @discord.Cog.listener()
    async def on_ready(self):
        """When ready to recieve data set debug channel and sync commands"""
        debug_guild = self.bot.get_guild(self.DEBUG_GUILD)
        self.debug_channel = (
            debug_guild.get_channel(self.DEBUG_CHANNEL) if debug_guild else None
        )

        if not getattr(self, "_conversation_state_loaded", False):
            print("Attempting to load from pickles")
            # Try to load self.full_conversation_history, self.conversation_threads, and self.conversation_thread_owners from the `pickles` folder
            try:
                with open(
                    EnvService.save_path()
                    / "pickles"
                    / "full_conversation_history.pickle",
                    "rb",
                ) as f:
                    self.full_conversation_history = pickle.load(f)
                    print("Loaded full_conversation_history")

                with open(
                    EnvService.save_path() / "pickles" / "conversation_threads.pickle",
                    "rb",
                ) as f:
                    self.conversation_threads = pickle.load(f)
                    print("Loaded conversation_threads")

                with open(
                    EnvService.save_path()
                    / "pickles"
                    / "conversation_thread_owners.pickle",
                    "rb",
                ) as f:
                    self.conversation_thread_owners = pickle.load(f)
                    print("Loaded conversation_thread_owners")

                with open(
                    EnvService.save_path() / "pickles" / "instructions.pickle",
                    "rb",
                ) as f:
                    self.instructions = pickle.load(f)
                    print("Loaded instructions")

                # Reject malformed saved state while allowing empty conversations.
                assert isinstance(self.full_conversation_history, dict)
                assert isinstance(self.conversation_threads, dict)
                assert isinstance(self.conversation_thread_owners, dict)
                self.full_conversation_history = defaultdict(
                    list, self.full_conversation_history
                )
                self.conversation_thread_owners = defaultdict(
                    list, self.conversation_thread_owners
                )
                for thread in self.conversation_threads.values():
                    if thread.model not in Models.TEXT_MODELS and (
                        thread.model != EnvService.get_openai_model()
                    ):
                        thread.model = self.model.model

            except Exception:
                print("Failed to load existing pickles")
                self.full_conversation_history = defaultdict(list)
                self.conversation_threads = {}
                self.conversation_thread_owners = defaultdict(list)
                print("Set empty dictionaries, pickles will be saved in the future")

            self._conversation_state_loaded = True
        recovery = getattr(self, "conversation_recovery", None)
        if recovery is not None:
            recovery.start()

        print("Syncing commands...")

        commands = list(self.bot.pending_application_commands)
        guild_ids = list(dict.fromkeys(ALLOWED_GUILDS))
        # Multicog groups can start without guild_ids. Normalize the original
        # commands too: Pycord's diff reads those objects rather than the copies
        # made by register_commands. Never invoke its global-first sync helper.
        for command in commands:
            command.guild_ids = guild_ids.copy()
        for guild_id in guild_ids if commands else []:
            try:
                await self.bot.register_commands(
                    commands=commands,
                    guild_id=guild_id,
                    method="bulk",
                    force=False,
                    delete_existing=True,
                )
            except Exception:
                traceback.print_exc()
                print(
                    f"Command registration failed for configured guild {guild_id}. Check that the bot belongs to this guild and has permission to register commands."
                )
        print("Commands synced")

        # Start an inline async loop that runs every 10 seconds to save the conversation history to a pickle file
        print("Starting pickle loop")
        if getattr(self, "_pickle_loop_running", False):
            return
        self._pickle_loop_running = True
        try:
            while True:
                await asyncio.sleep(15)
                await self.pickle_queue.put(
                    Pickler(
                        self.full_conversation_history,
                        self.conversation_threads,
                        self.conversation_thread_owners,
                        self.instructions,
                    )
                )

        finally:
            self._pickle_loop_running = False

    def check_conversing(self, channel_id, message_content):
        '''given channel id and a message, return true if it's a conversation thread, false if not, or if the message starts with "~"'''
        cond1 = channel_id in self.conversation_threads
        # If the trimmed message starts with a Tilde, then we want to not contribute this to the conversation
        try:
            cond2 = not message_content.strip().startswith("~")
        except Exception as e:
            print(e)
            cond2 = False

        return (cond1) and cond2

    async def _conversation_channel(self, ctx):
        """Resolve a local REST channel when an interaction has no Gateway guild."""
        channel = ctx.channel
        if isinstance(channel, (discord.Thread, discord.TextChannel)) and isinstance(
            channel.guild, discord.Guild
        ):
            return channel
        recovery = self.conversation_recovery
        payload = await recovery._http(self.bot.http.get_channel(channel.id))
        guild_id = int(payload.get("guild_id", 0))
        interaction = getattr(ctx, "interaction", ctx)
        expected_guild = getattr(ctx.guild, "id", None) or getattr(
            interaction, "guild_id", None
        )
        if (
            int(payload["id"]) != channel.id
            or guild_id not in EnvService.get_allowed_guilds()
            or (expected_guild is not None and guild_id != expected_guild)
        ):
            raise ValueError("Conversation channel identity does not match")
        guild = await recovery._rest_guild(guild_id)
        if payload["type"] in (
            discord.ChannelType.public_thread.value,
            discord.ChannelType.private_thread.value,
        ):
            return discord.Thread(guild=guild, state=self.bot._connection, data=payload)
        if payload["type"] == discord.ChannelType.text.value:
            return discord.TextChannel(
                guild=guild, state=self.bot._connection, data=payload
            )
        raise ValueError("Unsupported conversation channel type")

    async def end_conversation(
        self, ctx, opener_user_id=None, conversation_limit=False
    ):
        """Close a conversation; external controls acquire its dispatch lock first."""
        channel_id = ctx.channel.id
        user = getattr(ctx, "user", None) or getattr(ctx, "author", None)
        user_id = opener_user_id if opener_user_id is not None else user.id
        if channel_id not in self.conversation_threads:
            await TextService.respond_control(
                ctx, "This is not a conversation channel."
            )
            return
        if not conversation_limit:
            if channel_id not in self.conversation_thread_owners.get(user_id, ()):
                await TextService.respond_control(
                    ctx, "This is not a conversation thread that you own!"
                )
                return
            if TextService.conversation_busy(self, user_id, channel_id):
                await TextService.respond_control(
                    ctx,
                    "Please wait for the current response before ending the conversation.",
                )
                return

        # Component and slash contexts must acknowledge before any REST lookup.
        if isinstance(ctx, (discord.ApplicationContext, discord.Interaction)):
            if not ctx.response.is_done():
                await ctx.response.defer(ephemeral=True, invisible=False)
        try:
            channel = await self._conversation_channel(ctx)
        except Exception as error:
            print(f"Conversation channel lookup failed: {type(error).__name__}")
            await TextService.respond_control(
                ctx,
                "I couldn't verify this conversation channel. Try again or ask the bot operator.",
            )
            return

        conversation = self.conversation_threads.pop(channel_id)
        full_history = self.full_conversation_history.get(channel_id, ())
        start = getattr(conversation, "transcript_start", None)
        complete_transcript = type(start) is int and 0 <= start <= len(full_history)
        history = (
            full_history[start:]
            if complete_transcript
            else [item.text for item in conversation.history[1:]]
        )
        transcript_owner = None
        for owner_id, threads in self.conversation_thread_owners.items():
            if channel_id in threads:
                transcript_owner = owner_id
                threads.remove(channel_id)
                break
        await TextService.respond_control(
            ctx,
            "You have ended the conversation with GPT. Start a conversation with /gpt converse",
        )
        await channel.send(
            embed=EmbedStatics.generate_end_embed(),
            view=(
                ShareView(
                    channel_id,
                    transcript_owner,
                    history,
                    context_only=not complete_transcript,
                )
                if isinstance(channel, discord.Thread)
                else None
            ),
        )
        if isinstance(channel, discord.Thread):
            try:
                await channel.edit(name="Closed-GPT", archived=True)
            except Exception as error:
                print(f"Conversation archive failed: {type(error).__name__}")
                await TextService.respond_control(
                    ctx,
                    "The conversation has ended, but I couldn't archive its thread. Ask a server moderator to close it.",
                )

    async def send_settings_text(self, ctx):
        """Show supported writable settings without credentials or internal state."""
        names = editable_model_settings(type(self.model))
        embed = discord.Embed(
            title="GPTDiscord Settings",
            description="The current settings of the model",
            color=0x00FF00,
        )
        embed.add_field(name="Setting", value="\n".join(names), inline=True)
        embed.add_field(
            name="Value",
            value="\n".join(str(getattr(self.model, name)) for name in names),
            inline=True,
        )
        if Models.is_reasoning(self.model.model):
            embed.set_footer(
                text="This reasoning model ignores unsupported sampling settings."
            )
        await ctx.respond(embed=embed, ephemeral=True)

    async def process_settings(self, ctx, parameter, value):
        """Validate a supported setting through its model property setter."""
        if parameter not in editable_model_settings(type(self.model)):
            await ctx.respond(
                "The parameter is not a supported setting", ephemeral=True
            )
            return
        try:
            setattr(self.model, parameter, value)
        except ValueError as error:
            await ctx.respond(str(error), ephemeral=True)
            return
        await ctx.respond(
            f"Successfully set the parameter {parameter} to {value}", ephemeral=True
        )

    def generate_debug_message(self, prompt, response):
        """create a debug message with a prompt and a response field"""
        debug_message = "----------------------------------------------------------------------------------\n"
        debug_message += "Prompt:\n```\n" + prompt + "\n```\n"
        debug_message += "Response:\n```\n" + json.dumps(response, indent=4) + "\n```\n"
        return debug_message

    async def paginate_and_send(self, response_text, ctx):
        """paginate a response to a text cutoff length and send it in chunks"""
        from_context = isinstance(ctx, discord.ApplicationContext)

        response_text = [
            response_text[i : i + self.TEXT_CUTOFF]
            for i in range(0, len(response_text), self.TEXT_CUTOFF)
        ]
        # Send each chunk as a message
        first = False
        for chunk in response_text:
            if not first:
                if from_context:
                    await ctx.send_followup(chunk)
                else:
                    await ctx.reply(chunk)
                first = True
            else:
                if from_context:
                    response_message = await ctx.send_followup(chunk)
                else:
                    response_message = await ctx.channel.send(chunk)
        return response_message

    async def paginate_embed(self, response_text):
        """Given a response text make embed pages and return a list of the pages."""

        response_text = [
            response_text[i : i + self.EMBED_CUTOFF]
            for i in range(0, len(response_text), self.EMBED_CUTOFF)
        ]
        pages = []
        first = False
        # Send each chunk as a message
        for count, chunk in enumerate(response_text, start=1):
            if not first:
                page = discord.Embed(
                    title=f"Page {count}",
                    description=chunk,
                )
                first = True
            else:
                page = discord.Embed(
                    title=f"Page {count}",
                    description=chunk,
                )
            pages.append(page)

        return pages

    async def queue_debug_message(self, debug_message, debug_channel):
        """Put a message into the debug queue"""
        await self.message_queue.put(Message(debug_message, debug_channel))

    async def queue_debug_chunks(self, debug_message, debug_channel):
        """Put a message as chunks into the debug queue"""
        debug_message_chunks = [
            debug_message[i : i + self.TEXT_CUTOFF]
            for i in range(0, len(debug_message), self.TEXT_CUTOFF)
        ]

        backticks_encountered = 0

        for i, chunk in enumerate(debug_message_chunks):
            # Count the number of backticks in the chunk
            backticks_encountered += chunk.count("```")

            # If it's the first chunk, append a "\n```\n" to the end
            if i == 0:
                chunk += "\n```\n"

            # If it's an interior chunk, append a "```\n" to the end, and a "\n```\n" to the beginning
            elif i < len(debug_message_chunks) - 1:
                chunk = "\n```\n" + chunk + "```\n"

            # If it's the last chunk, append a "```\n" to the beginning
            else:
                chunk = "```\n" + chunk

            await self.message_queue.put(Message(chunk, debug_channel))

    async def send_debug_message(self, debug_message, debug_channel):
        """process a debug message and put directly into queue or chunk it"""
        # Send the debug message
        try:
            if len(debug_message) > self.TEXT_CUTOFF:
                await self.queue_debug_chunks(debug_message, debug_channel)
            else:
                await self.queue_debug_message(debug_message, debug_channel)
        except Exception as e:
            traceback.print_exc()
            await self.message_queue.put(
                Message("Error sending debug message: " + str(e), debug_channel)
            )

    async def check_conversation_limit(self, message):
        """Check if a conversation has reached the set limit and end it if it has"""
        # After each response, check if the user has reached the conversation limit in terms of messages or time.
        if message.channel.id in self.conversation_threads:
            # If the user has reached the max conversation length, end the conversation
            if (
                self.conversation_threads[message.channel.id].count
                >= self.model.max_conversation_length
            ):
                await message.reply(
                    "You have reached the maximum conversation length. You have ended the conversation with GPT, and it has ended."
                )
                await self.end_conversation(message, conversation_limit=True)
                return True
        return False

    async def summarize_conversation(self, message, prompt, custom_api_key=None):
        """Takes a conversation history filled prompt and summarizes it to then start a new history with it as the base"""
        response = await self.model.send_summary_request(
            prompt, custom_api_key=custom_api_key
        )
        summarized_text = response["choices"][0]["message"]["content"]

        history = self.conversation_threads[message.channel.id].history
        new_conversation_history = [history[0]]
        new_conversation_history.append(
            EmbeddedConversationItem(
                f"\nThis conversation has some context from earlier, which has been summarized as follows: {summarized_text} \nContinue the conversation, paying very close attention to things <username> told you, such as their name, and personal details.",
                0,
            )
        )

        # Get the last entry from the thread's conversation history
        new_conversation_history.append(
            EmbeddedConversationItem(
                history[-1].text + "\n",
                history[-1].timestamp,
                image_urls=history[-1].image_urls,
            )
        )
        self.conversation_threads[message.channel.id].history = new_conversation_history

    # A listener for message edits to redo prompts if they are edited
    @discord.Cog.listener()
    async def on_message_edit(self, before, after):
        if not await message_has_access(after):
            return
        if after.channel.id in self.conversation_threads:
            await self.conversation_recovery.dispatch(after, edited=True)
        else:
            await self._process_message_edit(after)

    async def _process_message_edit(self, after):
        """Process one authorized conversation edit under its ingress lock."""
        if not await message_has_access(after):
            return

        if after.author.id == self.bot.user.id:
            return

        # Moderation
        if after.guild is not None:
            if (
                after.guild.id in Moderation.moderation_queues
                and Moderation.moderation_queues[after.guild.id] is not None
            ):
                # Create a timestamp that is 0.25 seconds from now
                timestamp = (
                    datetime.datetime.now() + datetime.timedelta(seconds=0.25)
                ).timestamp()
                await Moderation.moderation_queues[after.guild.id].put(
                    Moderation(after, timestamp)
                )  # TODO Don't proceed if message was deleted!

        await TextService.process_conversation_edit(
            self,
            after,
            original_message,
            USER_INPUT_API_KEYS=USER_INPUT_API_KEYS,
            USER_KEY_DB=USER_KEY_DB,
        )

    @discord.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not await message_has_access(message):
            return
        if message.channel.id in self.conversation_threads:
            await self.conversation_recovery.dispatch(message)
        else:
            await self._process_message(message)

    async def _process_message(self, message, *, conversation_only=False):
        """Process one authorized message; REST recovery never routes bot tags."""
        if not await message_has_access(message):
            return
        if message.author == self.bot.user:
            return

        if message.author.bot:
            return

        # Discord replies are ordinary conversation turns too.
        if message.type not in (
            discord.MessageType.default,
            discord.MessageType.reply,
        ):
            return

        # Moderations service is done here.
        if (
            hasattr(message, "guild")
            and message.guild
            and message.guild.id in Moderation.moderation_queues
            and Moderation.moderation_queues[message.guild.id] is not None
        ):
            # Don't moderate if there is no "roles" attribute for the author
            if not hasattr(message.author, "roles"):
                pass
            # Verify that the user is not in a role that can bypass moderation
            elif CHAT_BYPASS_ROLES == [None] or not has_any_role(
                message, CHAT_BYPASS_ROLES
            ):
                # Create a timestamp that is 0.5 seconds from now
                timestamp = (
                    datetime.datetime.now() + datetime.timedelta(seconds=0.5)
                ).timestamp()
                await Moderation.moderation_queues[message.guild.id].put(
                    Moderation(message, timestamp)
                )

        # Language check
        if FORCE_ENGLISH and len(message.content.split(" ")) > 3:
            if not await Moderation.force_english_and_respond(
                message.content, self.LANGUAGE_DETECT_STARTER_TEXT, message
            ):
                await message.delete()
                return

        amended_message = message.content

        # Retain only image attachments if in a regular conversation
        attachments = []
        if self.check_conversing(message.channel.id, message.content):
            for _file in message.attachments:
                content_type = _file.content_type or ""
                if content_type.startswith("image/"):
                    attachments.append(_file)
                elif content_type.startswith("text/"):
                    # Decoding a text file to use in the prompt
                    text = (await read_attachment_bytes(_file)).decode("utf-8")
                    amended_message += text

        # Process the message if the user is in a conversation
        conversation_processed = await TextService.process_conversation_message(
            self,
            message,
            USER_INPUT_API_KEYS,
            USER_KEY_DB,
            files=None if len(attachments) < 1 else attachments,
            amended_message=amended_message,
        )
        if conversation_processed:
            guild_name = (
                getattr(message.guild, "name", None)
                or (getattr(message, "_raw_data", None) or {}).get("guild_id")
                or "Direct messages"
            )
            print("Processed a conversation message in server", guild_name)
            self.usage_service.update_usage_memory(
                guild_name, "conversation_message", 1
            )
            original_message[message.author.id] = message.id

        if conversation_only:
            return conversation_processed

        # If the user tagged the bot and the tag wasn't an @here or @everyone, retrieve the message
        if f"<@{self.bot.user.id}>" in message.content and not (
            "@everyone" in message.content or "@here" in message.content
        ):
            if (
                not BOT_TAGGABLE or conversation_processed
            ):  # Don't go through the tagging system if we're in a conversation
                return

            # Check if any of the message author's role names are in BOT_TAGGABLE_ROLES, if not, return
            if BOT_TAGGABLE_ROLES != [None] and not has_any_role(
                message, BOT_TAGGABLE_ROLES
            ):
                return

            # Remove the mention from the message
            prompt = message.content.replace(self.bot.user.mention, "")
            # If the message is empty, don't process it
            if len(prompt) < 5:
                await message.reply(
                    "This is too short of a prompt to think about. Please be more specific."
                )
                return

            await self.ask_command(
                message,
                prompt=prompt,
                from_message_context=True,
            )

    def cleanse_response(self, response_text):
        """Cleans history tokens from response"""
        response_text = response_text.replace("<yourname>:", "")
        response_text = response_text.replace("You:", "")
        response_text = response_text.replace(BOT_NAME.replace(" ", ""), "")
        response_text = response_text.replace(BOT_NAME, "")
        response_text = response_text.replace("<|endofstatement|>", "")
        return response_text

    def remove_awaiting(
        self, author_id, channel_id, from_ask_command, from_edit_command
    ):
        """Remove user from ask/edit command response wait, if not any of those then process the id to remove user from thread response wait"""
        safe_remove_list(self.awaiting_responses, author_id)
        if not from_ask_command and not from_edit_command:
            safe_remove_list(self.awaiting_thread_responses, channel_id)

    async def mention_to_username(self, ctx, message):
        """replaces discord mentions with their server nickname in text, if the user is not found keep the mention as is"""
        if not discord.utils.raw_mentions(message):
            return message
        for mention in discord.utils.raw_mentions(message):
            try:
                user = await discord.utils.get_or_fetch(ctx.guild, "member", mention)
                message = message.replace(f"<@{str(mention)}>", user.display_name)
            except Exception:
                pass
        return message

    # COMMANDS

    async def help_command(self, ctx):
        """Command handler. Generates a help message and sends it to the user"""
        await ctx.defer()
        embed = discord.Embed(
            title="GPTDiscord Help", description="The current commands", color=0xC730C7
        )
        embed.add_field(
            name="/code chat",
            value="Chat with GPT hooked up to a full execution environment!",
            inline=False,
        )
        embed.add_field(
            name="/internet chat",
            value="AI-Assisted chatting connected to google, wolfram, and a crawler!",
            inline=False,
        )
        embed.add_field(
            name="/web",
            value="Search the web with OpenAI and receive source links.",
            inline=False,
        )
        embed.add_field(
            name="/internet search",
            value="Do a one-off internet search assisted by GPT!",
            inline=False,
        )
        embed.add_field(
            name="/index chat",
            value="Chat with your documents",
            inline=False,
        )
        embed.add_field(
            name="/index",
            value="More indexing/working with documents commands",
            inline=False,
        )
        embed.add_field(
            name="/gpt converse",
            value="Have a multi-modal conversation with GPT",
            inline=False,
        )
        embed.add_field(
            name="/gpt ask",
            value="Ask GPT something. Be clear, long, and concise in your prompt.",
            inline=False,
        )
        embed.add_field(
            name="/gpt",
            value="More pure GPT commands",
            inline=False,
        )
        embed.add_field(
            name="/dalle draw",
            value="Generate images with the configured GPT Image model.",
            inline=False,
        )
        embed.add_field(
            name="/dalle optimize",
            value="Improve a prompt for image generation.",
            inline=False,
        )
        embed.add_field(
            name="/system settings",
            value="Print the current settings of the model",
            inline=False,
        )
        embed.add_field(
            name="/system settings <model parameter> <value>",
            value="Change the parameter of the model named by <model parameter> to new value <value>",
            inline=False,
        )
        embed.add_field(
            name="/mod",
            value="The automatic moderations service",
            inline=False,
        )
        embed.add_field(
            name="/translate",
            value="Translate from one language to another",
            inline=False,
        )
        embed.add_field(
            name="/transcribe",
            value="Transcription services (very experimental)",
            inline=False,
        )

        embed.add_field(name="/help", value="See this help text", inline=False)
        await ctx.respond(embed=embed, ephemeral=False)

    async def set_usage_command(
        self, ctx: discord.ApplicationContext, usage_amount: float
    ):
        """Command handler. Sets the usage file to the given value"""
        await ctx.defer()

        # Attempt to convert the input usage value into a float
        try:
            usage = float(usage_amount)
            await self.usage_service.set_usage(usage)
            await ctx.respond(f"Set the usage to {usage}")
        except Exception:
            await ctx.respond("The usage value must be a valid float.")
            return

    async def delete_all_conversation_threads_command(
        self, ctx: discord.ApplicationContext
    ):
        """Delete matching active threads only when this bot owns them."""
        await ctx.defer(ephemeral=True)
        if ctx.guild is None:
            await ctx.respond(
                "Discord isn't delivering this server's thread list. No threads were deleted. "
                "A server moderator can delete them in Discord.",
                ephemeral=True,
            )
            return
        deleted = 0
        for thread in ctx.guild.threads:
            name = thread.name.lower()
            if thread.owner_id != self.bot.user.id or not (
                "with gpt" in name or "closed-gpt" in name
            ):
                continue
            try:
                await thread.delete()
                deleted += 1
            except discord.HTTPException as error:
                print(f"Conversation thread deletion failed: {type(error).__name__}")
        await ctx.respond(
            f"Deleted {deleted} active conversation threads created by this bot.",
            ephemeral=True,
        )

    async def usage_command(self, ctx):
        """Report recorded provider cost without inventing mixed-model token counts."""
        await ctx.defer(ephemeral=True)
        usage = await self.usage_service.get_usage()
        embed = discord.Embed(
            title="GPTDiscord Usage",
            description="Recorded API cost estimate. Check your provider dashboard for billed usage.",
            color=0x00FF00,
        )
        embed.add_field(name="Recorded cost", value=f"${usage:.6f}", inline=False)
        await ctx.respond(embed=embed, ephemeral=True)

    async def instruction_command(
        self,
        ctx: discord.ApplicationContext,
        mode: str,
        type: str,
        instruction: str,
        instruction_file: discord.Attachment,
        private: bool,
    ):
        """Command to let users set their own system prompt or add one to the channel"""

        await ctx.defer(ephemeral=private)

        if mode == "set" and not (instruction or instruction_file):
            await ctx.respond(
                "You must include either an **instruction** or an **instruction file**"
            )
            return

        # Check if any of the message author's role names are in CHANNEL_INSTRUCTION_ROLES, if not, continue as user
        if type == "channel" and mode in ["set", "clear"]:
            if CHANNEL_INSTRUCTION_ROLES != [None] and not has_any_role(
                ctx, CHANNEL_INSTRUCTION_ROLES
            ):
                await ctx.respond(
                    "You don't have permisson to set the channel instruction. Defaulting to setting a user instruction"
                )
                type = "user"

        if instruction_file:
            bytestring = await read_attachment_bytes(instruction_file)
            file_instruction = bytestring.decode("utf-8")
        if instruction and instruction_file:
            instruction = f"{file_instruction}\n\n{instruction}"
        elif instruction_file:
            instruction = file_instruction

        # If premoderation is enabled, check
        if PRE_MODERATE and mode == "set":
            if await Moderation.simple_moderate_and_respond(instruction, ctx):
                return

        if type == "channel":
            set_id = ctx.channel.id
        else:
            set_id = ctx.user.id

        if mode == "set":
            self.instructions[set_id] = Instruction(set_id, instruction)
            await ctx.respond(f"The system instruction is set for **{type}**")
        elif mode == "get":
            try:
                instruction = self.instructions[set_id].prompt
                embed_pages = await self.paginate_embed(instruction)
                paginator = pages.Paginator(
                    pages=embed_pages, timeout=None, author_check=False
                )
                await paginator.respond(ctx.interaction)
            except Exception:
                await ctx.respond("There is no instruction set")
        elif mode == "clear":
            if self.instructions.pop(set_id, None) is None:
                await ctx.respond("There is no instruction set")
            else:
                await ctx.respond(f"The instruction has been removed for **{type}**")

    async def ask_command(
        self,
        ctx: discord.ApplicationContext,
        prompt: str,
        private: bool = False,
        temperature: float = None,
        top_p: float = None,
        frequency_penalty: float = None,
        presence_penalty: float = None,
        from_ask_action=None,
        from_other_action=None,
        from_message_context=None,
        prompt_file: discord.Attachment = None,
        model=None,
        prompt_token_limit=None,
        prompt_limit_message=None,
    ):
        """Command handler. Requests and returns a generation with no extras to the completion endpoint

        Args:
            ctx (discord.ApplicationContext): Command interaction
            prompt (str): A prompt to use for generation
            temperature (float): Sets the temperature override
            top_p (float): Sets the top p override
            frequency_penalty (float): Sets the frequency penalty override
            presence_penalty (float): Sets the presence penalty override
            from_action (bool, optional): Enables ephemeral. Defaults to None.
        """
        is_context = isinstance(ctx, discord.ApplicationContext)

        user = ctx.user if is_context else ctx.author

        if not (prompt or prompt_file):
            await ctx.respond(
                "You must include either a **prompt** or a **prompt file**"
            )
            return

        user_api_key = None
        if USER_INPUT_API_KEYS:
            user_api_key = await TextService.get_user_api_key(user.id, ctx, USER_KEY_DB)
            if not user_api_key:
                return

        if is_context:
            await ctx.defer(ephemeral=private)

        if prompt_file:
            bytestring = await read_attachment_bytes(prompt_file)
            file_prompt = bytestring.decode("utf-8")
        if prompt and prompt_file:
            prompt = f"{file_prompt}\n\n{prompt}"
        elif prompt_file:
            prompt = file_prompt

        prompt = await self.mention_to_username(ctx, prompt.strip())

        if len(prompt) < self.model.prompt_min_length:
            alias = ctx.respond if is_context else ctx.send
            await alias(
                f"Prompt must be greater than {self.model.prompt_min_length} characters, it is currently: {len(prompt)} characters"
            )
            return

        if prompt_token_limit is not None:
            tokens = self.model.usage_service.count_tokens(prompt)
            if tokens > prompt_token_limit:
                await ctx.respond(
                    prompt_limit_message.format(
                        tokens=tokens, limit=prompt_token_limit
                    ),
                    ephemeral=True,
                    delete_after=10,
                )
                return

        # If premoderation is enabled, check
        if PRE_MODERATE:
            if await Moderation.simple_moderate_and_respond(prompt, ctx):
                return

        overrides = Override(temperature, top_p, frequency_penalty, presence_penalty)

        await TextService.encapsulated_send(
            self,
            user.id,
            prompt,
            ctx,
            overrides=overrides,
            from_ask_command=True,
            custom_api_key=user_api_key,
            from_ask_action=from_ask_action,
            from_other_action=from_other_action,
            from_message_context=from_message_context,
            model=model,
        )

    async def edit_command(
        self,
        ctx: discord.ApplicationContext,
        instruction: str,
        text: str,
        private: bool,
        temperature: float,
        top_p: float,
    ):
        """Command handler. Requests and returns a generation with no extras to the edit endpoint

        Args:
            ctx (discord.ApplicationContext): Command interaction
            instruction (str): The modification instructions
            text (str): The text that should be modified
            temperature (float): Sets the temperature override
            top_p (float): Sets the top p override
        """
        user = ctx.user

        user_api_key = None
        if USER_INPUT_API_KEYS:
            user_api_key = await TextService.get_user_api_key(user.id, ctx, USER_KEY_DB)
            if not user_api_key:
                return

        await ctx.defer(ephemeral=private)

        text = await self.mention_to_username(ctx, text.strip())
        instruction = await self.mention_to_username(ctx, instruction.strip())

        # Validate that  all the parameters are in a good state before we send the request
        if len(instruction) < self.model.prompt_min_length:
            await ctx.respond(
                f"Instruction must be at least {self.model.prompt_min_length} characters long"
            )
            return

        if PRE_MODERATE:
            if await Moderation.simple_moderate_and_respond(instruction + text, ctx):
                return

        overrides = Override(temperature, top_p, 0, 0)

        await TextService.encapsulated_send(
            self,
            user.id,
            prompt=text,
            ctx=ctx,
            overrides=overrides,
            instruction=instruction,
            from_edit_command=True,
            custom_api_key=user_api_key,
        )

    async def private_test_command(self, ctx: discord.ApplicationContext):
        """Command handler. Creates a private thread in the current channel"""
        await ctx.defer(ephemeral=True)
        await ctx.respond("Your private test thread")
        thread = await ctx.channel.create_thread(
            name=ctx.user.name + "'s private test conversation",
            auto_archive_duration=60,
        )
        await thread.send(
            f"<@{str(ctx.user.id)}> This is a private thread for testing. Only you and server admins can see this thread."
        )

    async def converse_command(
        self,
        ctx: discord.ApplicationContext,
        draw: bool,
        opener: str,
        opener_file: str,
        private: bool,
        minimal: bool,
        model: str,
        temperature: float,
        top_p: float,
        frequency_penalty: float,
        presence_penalty: float,
        use_threads: bool = True,
    ):
        """Command handler. Starts a conversation with the bot

        Args:
            ctx (discord.ApplicationContext): Command interaction
            opener (str): The first prompt to send in the conversation
            opener_file (str): A .txt or .json file which is appended before the opener
            private (bool): If the thread should be private
            minimal (bool): If a minimal starter should be used
            model (str): The openai model that should be used
            temperature (float): Sets the temperature override
            top_p (float): Sets the top p override
            frequency_penalty (float): Sets the frequency penalty override
            presence_penalty (float): Sets the presence penalty override
        """

        if private and not use_threads:
            await ctx.respond(
                "Private conversations require a private thread. Keep `use_threads` enabled, "
                "or set `private` to false for a channel conversation.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        guild_id = getattr(ctx.guild, "id", None) or getattr(
            getattr(ctx, "interaction", None), "guild_id", None
        )
        missing_gateway = guild_id is not None and self.bot.get_guild(guild_id) is None
        if missing_gateway and (
            not use_threads or not self.conversation_recovery.enabled_for(guild_id)
        ):
            await ctx.respond(
                "Discord is not delivering this server's messages to me. "
                "A bot operator can enable thread conversation recovery for this server. "
                "You can use `/gpt ask` meanwhile.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        user = ctx.user

        # If we are in user input api keys mode, check if the user has entered their api key before letting them continue
        user_api_key = None
        if USER_INPUT_API_KEYS:
            user_api_key = await TextService.get_user_api_key(user.id, ctx, USER_KEY_DB)
            if not user_api_key:
                return

        await ctx.defer(ephemeral=private or missing_gateway)
        if missing_gateway:
            try:
                recovered = await self.conversation_recovery.prepare_context(ctx)
            except Exception as error:
                print("Conversation REST setup failed", type(error).__name__)
                recovered = False
            if not recovered:
                await ctx.respond(
                    "I couldn't verify this server's recovery access or channel permissions. "
                    "Ask the bot operator to check the recovery configuration; `/gpt ask` is still available.",
                    ephemeral=True,
                )
                return
            user = ctx.user

        # Resolve the saved opener after key setup/acknowledgement and before
        # moderation or creating a thread. JSON values become local overrides.
        if opener_file:
            if not opener_file.endswith((".txt", ".json")):
                opener_file = (
                    None  # Just start a regular thread if the file fails to load
                )
            else:
                # Load the file and read it into opener
                try:
                    opener_file = re.sub(
                        ".+(?=[\\//])", "", opener_file
                    )  # remove paths from the opener file
                    opener_file = EnvService.find_shared_file(
                        f"openers{separator}{opener_file}"
                    )
                    opener_file = await self.load_file(opener_file, ctx)
                    try:  # Try opening as json, if it fails it'll just pass the whole txt or json to the opener
                        opener_file = json.loads(opener_file)
                        temperature = opener_file.get("temperature", None)
                        top_p = opener_file.get("top_p", None)
                        frequency_penalty = opener_file.get("frequency_penalty", None)
                        presence_penalty = opener_file.get("presence_penalty", None)
                        if (
                            not opener
                        ):  # if we only use opener_file then only pass on opener_file for the opening prompt
                            opener = opener_file.get("text", "error getting text")
                        else:
                            opener = (
                                opener_file.get("text", "error getting text") + opener
                            )
                    except Exception:  # Parse as just regular text
                        if not opener:
                            opener = opener_file
                        else:
                            opener = opener_file + opener
                except Exception:
                    opener_file = (
                        None  # Just start a regular thread if the file fails to load
                    )

        # Check the opener for bad content.
        if PRE_MODERATE and opener is not None:
            if await Moderation.simple_moderate_and_respond(opener, ctx):
                return

        if use_threads:
            if private:
                embed_title = f"{user.name}'s private conversation with GPT"
                thread = await ctx.channel.create_thread(
                    name=embed_title,
                    auto_archive_duration=60,
                )
                try:
                    await thread.add_user(user)
                except Exception as error:
                    print(
                        f"Private conversation membership failed: {type(error).__name__}"
                    )
                    try:
                        await thread.delete()
                    except Exception as cleanup_error:
                        print(
                            f"Private conversation cleanup failed: {type(cleanup_error).__name__}"
                        )
                    await ctx.respond(
                        "I couldn't add you to the private thread, so the conversation wasn't started. "
                        "Ask a server moderator to check my thread permissions.",
                        ephemeral=True,
                    )
                    return
                await safe_ctx_respond(
                    ctx=ctx,
                    content=f"Your private conversation is ready: <#{thread.id}>.",
                    ephemeral=True,
                )
                target = thread
            else:
                embed_title = f"{user.name}'s conversation with GPT"
                message_embed = discord.Embed(
                    title=embed_title,
                    description=f"**Model**: {self.model.model if not model else model}",
                    color=0x808080,
                )
                message_embed.set_thumbnail(url="https://i.imgur.com/wpp4TIf.png")
                footer_text = (
                    "Chat with image understanding"
                    if Models.supports_vision(model or self.model.model)
                    or image_understanding_model.get_is_usable()
                    else "Chat"
                )
                message_embed.set_footer(
                    text=footer_text, icon_url="https://i.imgur.com/wpp4TIf.png"
                )
                message_thread = await ctx.send(embed=message_embed)
                thread = await message_thread.create_thread(
                    name=user.name + "'s conversation with GPT",
                    auto_archive_duration=60,
                )
                await safe_ctx_respond(ctx=ctx, content="Conversation started.")
                target = thread
        else:
            # Check if this current channel is already in a conversation
            if ctx.channel.id in self.conversation_threads:
                await ctx.respond(
                    "There is already a conversation in this channel. Please finish that conversation before starting a new one."
                )
                return

            # Check if the user is permitted to start a conversation in full channels
            # check if any of the user role names match CHANNEL_CHAT_ROLES
            if CHANNEL_CHAT_ROLES and CHANNEL_CHAT_ROLES != [None]:
                if not has_any_role(ctx, CHANNEL_CHAT_ROLES):
                    await ctx.respond(
                        "You are not permitted to start a conversation in this channel."
                    )
                    return

            target = ctx.channel
            if private:
                embed_title = f"{user.name}'s private conversation with GPT"
            else:
                embed_title = f"{user.name}'s conversation with GPT"

            embed = discord.Embed(title=embed_title, color=0x808080)
            await ctx.respond(embed=embed)

        # Setup and its opener share the same lock as incoming turns and controls.
        # Acquire it before publishing any conversation state to those entrypoints.
        async with (
            self.conversation_recovery.lock_for(target.id),
            self.conversation_recovery.active_control(),
        ):
            if target.id in self.conversation_threads:
                await ctx.respond(
                    "There is already a conversation in this channel. Please finish that conversation before starting a new one."
                )
                return
            model_selection = self.model.model if not model else model

            self.conversation_threads[target.id] = Thread(target.id)
            self.conversation_threads[target.id].transcript_start = len(
                self.full_conversation_history.get(target.id, ())
            )
            self.conversation_threads[target.id].model = model_selection
            if draw:
                self.conversation_threads[target.id].drawable = True

            # Set the overrides for the conversation
            self.conversation_threads[target.id].set_overrides(
                temperature, top_p, frequency_penalty, presence_penalty
            )

            if opener or opener_file:
                user_id_normalized = ctx.author.id
            else:
                user_id_normalized = user.id
            # Append the starter text for gpt to the user's history so it gets concatenated with the prompt later
            if minimal or opener_file or opener:
                starting_text = self.CONVERSATION_STARTER_TEXT_MINIMAL
            elif not Models.supports_vision(model_selection):
                starting_text = self.CONVERSATION_STARTER_TEXT
            else:  # Vision case, dont add the image-ocr image-caption, etc helpers here.
                starting_text = self.CONVERSATION_STARTER_TEXT_VISION

            if draw:
                starting_text += self.CONVERSATION_DRAWING_ABILITY_SNIPPET
            else:
                starting_text += "Image generation is disabled in this conversation. Ask the user to start a conversation with the `draw` option turned on to enable it."
            self.conversation_threads[target.id].history.append(
                EmbeddedConversationItem(starting_text, 0)
            )

            # Set user as thread owner before sending anything that can error and leave the thread unowned
            self.conversation_thread_owners[user_id_normalized].append(target.id)
            if isinstance(target, discord.Thread):
                await self.conversation_recovery.watch_thread(target, new=True)
            overrides = self.conversation_threads[target.id].get_overrides()

            await target.send(f"<@{str(ctx.user.id)}> is the thread owner.")

            await target.send(
                embed=EmbedStatics.generate_conversation_embed(
                    self.conversation_threads, target, opener, overrides
                )
            )

            # send opening
            if opener:
                self.conversation_threads[target.id].has_opener = True
                opener = await self.mention_to_username(ctx, opener)
                try:
                    target_message = await target.send(
                        embed=EmbedStatics.generate_opener_embed(opener)
                    )
                except:
                    target_message = await target.send(
                        embed=EmbedStatics.generate_opener_embed(
                            opener[:1900] + " [...]"
                        )
                    )
                if target.id in self.conversation_threads:
                    self.awaiting_responses.append(user_id_normalized)
                    if not self.pinecone_service:
                        self.conversation_threads[target.id].history.append(
                            EmbeddedConversationItem(
                                f"\n{ctx.author.display_name}: {opener} <|endofstatement|>\n",
                                0,
                            )
                        )
                    self.awaiting_thread_responses.append(target.id)

                    # ... (no other changes in the middle part of the function)

                overrides = Override(
                    overrides["temperature"],
                    overrides["top_p"],
                    overrides["frequency_penalty"],
                    overrides["presence_penalty"],
                )

                self.full_conversation_history[target.id].append(opener)
                try:
                    await TextService.encapsulated_send(
                        self,
                        target.id,
                        (
                            opener
                            if target.id not in self.conversation_threads
                            or self.pinecone_service
                            else "".join(
                                [
                                    item.text
                                    for item in self.conversation_threads[
                                        target.id
                                    ].history
                                ]
                            )
                        ),
                        target_message,
                        overrides=overrides,
                        user=user,
                        model=self.conversation_threads[target.id].model,
                        custom_api_key=user_api_key,
                        is_drawable=draw,
                    )
                finally:
                    safe_remove_list(self.awaiting_responses, user_id_normalized)
                    safe_remove_list(self.awaiting_thread_responses, target.id)

    async def end_command(self, ctx: discord.ApplicationContext):
        """End a conversation without racing its active generation or controls."""
        channel_id, user_id = ctx.channel.id, ctx.user.id
        if channel_id not in self.conversation_threads:
            await TextService.respond_control(
                ctx, "This is not a conversation channel."
            )
            return
        lock = TextService.conversation_lock(self, channel_id)
        if lock.locked() or TextService.conversation_busy(self, user_id, channel_id):
            await TextService.respond_control(
                ctx,
                "Please wait for the current response before ending the conversation.",
            )
            return
        await ctx.defer(ephemeral=True)
        async with lock:
            try:
                async with self.conversation_recovery.active_control():
                    await self.end_conversation(ctx)
            except Exception as error:
                print(f"Conversation end failed: {type(error).__name__}")
                await TextService.respond_control(
                    ctx,
                    "I couldn't finish closing this conversation. Try again or ask the bot operator.",
                )

    async def setup_command(self, ctx: discord.ApplicationContext):
        """Command handler. Opens the setup modal"""
        if not USER_INPUT_API_KEYS:
            await ctx.respond(
                "This server doesn't support user input API keys.",
                ephemeral=True,
                delete_after=30,
            )
            return

        modal = SetupModal(user_key_db=USER_KEY_DB)
        await ctx.send_modal(modal)

    async def settings_command(
        self, ctx: discord.ApplicationContext, parameter: str = None, value: str = None
    ):
        """Command handler. Returns current settings or sets new values"""
        await ctx.defer()
        if parameter is None and value is None:
            await self.send_settings_text(ctx)
            return

        # If only one of the options are set, then this is invalid.
        if (
            parameter is None
            and value is not None
            or parameter is not None
            and value is None
        ):
            await ctx.respond(
                "Invalid settings command. Please use `/settings <parameter> <value>` to change a setting"
            )
            return

        # Otherwise, process the settings change
        await self.process_settings(ctx, parameter, value)

    async def settings_reset_command(self, ctx: discord.ApplicationContext):
        """Command handler. Resets all settings to default"""
        await ctx.defer()
        self.model.reset_settings()
        await ctx.respond("Settings reset to default")

    #
    # Text-based context menu commands from here
    #

    async def ask_gpt_action(self, ctx, message: discord.Message):
        """Message command. Return the message"""
        prompt = message.content
        await self.ask_command(
            ctx,
            prompt=prompt,
            from_ask_action=prompt,
        )

    async def paraphrase_action(self, ctx, message: discord.Message):
        """Message command. paraphrase the current message content"""
        prompt = message.content
        from_other_action = prompt + "\nParaphrased:"

        # Construct the paraphrase prompt
        prompt = f"Paraphrase the following text. Maintain roughly the same text length after paraphrasing and the same tone of voice: {prompt} \nParaphrased:"

        await self.ask_command(
            ctx,
            prompt=prompt,
            from_other_action=from_other_action,
            prompt_token_limit=self.model.max_tokens - 1000,
            prompt_limit_message="This message is too long to paraphrase.",
        )

    async def elaborate_action(self, ctx, message: discord.Message):
        """Message command. elaborate on the subject of the current message content"""
        prompt = message.content
        from_other_action = prompt + "\nElaboration:"

        # Construct the paraphrase prompt
        prompt = f"Elaborate with more information about the subject of the following message. Be objective and detailed and respond with elaborations only about the subject(s) of the message: {prompt} \n\nElaboration:"

        await self.ask_command(
            ctx,
            prompt=prompt,
            from_other_action=from_other_action,
            prompt_token_limit=self.model.max_tokens - 500,
            prompt_limit_message="This message is too long to elaborate on.",
        )

    async def summarize_action(self, ctx, message: discord.Message):
        """Message command. elaborate on the subject of the current message content"""
        prompt = message.content
        from_other_action = (
            "Message at message link: " + message.jump_url + "\nSummarized:"
        )

        # Construct the paraphrase prompt
        prompt = f"Summarize the following message, be as short and concise as possible: {prompt} \n\nSummary:"

        await self.ask_command(
            ctx,
            prompt=prompt,
            from_other_action=from_other_action,
            prompt_token_limit=self.model.max_tokens - 300,
            prompt_limit_message="Your prompt is too long. It has {tokens} tokens, but the maximum is {limit}.",
        )


class ShareView(discord.ui.View):
    def __init__(self, conversation_id, owner_id, history, *, context_only=False):
        super().__init__(timeout=3600)
        self.add_item(
            ShareButton(conversation_id, owner_id, history, context_only=context_only)
        )

    async def on_timeout(self):
        self.clear_items()


class ShareButton(discord.ui.Button["ShareView"]):
    def __init__(self, conversation_id, owner_id, history, *, context_only=False):
        super().__init__(
            style=discord.ButtonStyle.green,
            label="Download Conversation",
            custom_id="share_conversation",
        )
        self.conversation_id = conversation_id
        self.owner_id = owner_id
        self.history = tuple(history)
        self.context_only = context_only

    async def callback(self, interaction: discord.Interaction):
        if (
            interaction.user.id != self.owner_id
            or interaction.channel.id != self.conversation_id
        ):
            await TextService.respond_control(
                interaction, "Only the conversation owner can download this transcript."
            )
            return
        await interaction.response.defer(ephemeral=True, invisible=False)
        if not self.history:
            await interaction.followup.send(
                "There are no conversation messages to download.", ephemeral=True
            )
            return
        # A failed generation can leave consecutive human entries. Preserve
        # their order without guessing alternating speaker roles.
        text = "\n\n".join(str(message) for message in self.history)
        if self.context_only:
            text = (
                "Recovered conversation context. Earlier turns may have been summarized.\n\n"
                + text
            )
        data = text.encode("utf-8")
        limit = interaction.attachment_size_limit or getattr(
            interaction.guild, "filesize_limit", 10 * 1024 * 1024
        )
        if len(data) > limit:
            await interaction.followup.send(
                "This transcript exceeds Discord's upload limit.", ephemeral=True
            )
            return
        with io.BytesIO(data) as stream:
            await interaction.followup.send(
                file=discord.File(
                    stream, filename=f"conversation-{self.conversation_id}.txt"
                ),
                ephemeral=True,
            )
