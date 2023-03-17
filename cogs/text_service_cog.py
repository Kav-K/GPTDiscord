import asyncio
import datetime
import pickle
import re
import traceback
import sys
from pathlib import Path


import aiofiles
import json

import discord
from discord import ClientUser

from models.deepl_model import TranslationModel
from models.embed_statics_model import EmbedStatics
from models.openai_model import Override
from services.environment_service import EnvService
from services.message_queue_service import Message
from services.moderations_service import Moderation
from models.user_model import Thread, EmbeddedConversationItem
from collections import defaultdict
from sqlitedict import SqliteDict

from services.pickle_service import Pickler
from services.sharegpt_service import ShareGPTService
from services.text_service import SetupModal, TextService

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
BOT_TAGGABLE_ROLES = EnvService.get_gpt_roles()


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
        self.summarize = self.model.summarize_conversations

        # Pinecone data
        self.pinecone_service = pinecone_service

        # Sharing service
        self.sharegpt_service = ShareGPTService()

        try:
            conversation_file_path = EnvService.find_shared_file(
                "conversation_starter_pretext.txt"
            )
            # Attempt to read a conversation starter text string from the file.
            with conversation_file_path.open("r") as f:
                self.CONVERSATION_STARTER_TEXT = f.read()
                print(
                    f"Conversation starter text loaded from {conversation_file_path}."
                )
            assert self.CONVERSATION_STARTER_TEXT is not None

            language_detect_file_path = EnvService.find_shared_file(
                "language_detection_pretext.txt"
            )
            # Attempt to read a conversation starter text string from the file.
            with language_detect_file_path.open("r") as f:
                self.LANGUAGE_DETECT_STARTER_TEXT = f.read()
                print(
                    f"Language detection starter text loaded from {language_detect_file_path}."
                )
            assert self.LANGUAGE_DETECT_STARTER_TEXT is not None

            conversation_file_path_minimal = EnvService.find_shared_file(
                "conversation_starter_pretext_minimal.txt"
            )
            with conversation_file_path_minimal.open("r") as f:
                self.CONVERSATION_STARTER_TEXT_MINIMAL = f.read()
                print(
                    f"Conversation starter text loaded from {conversation_file_path_minimal}."
                )
            assert self.CONVERSATION_STARTER_TEXT_MINIMAL is not None

        except Exception:
            self.CONVERSATION_STARTER_TEXT = self.CONVERSATION_STARTER_TEXT_MINIMAL = (
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

    async def load_file(self, file, ctx):
        """Take filepath, return content or respond if not found"""
        try:
            async with aiofiles.open(file, "r") as f:
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
        if self.model.welcome_message_enabled:
            query = f"Please generate a welcome message for {member.name} who has just joined the server."

            try:
                welcome_message_response = await self.model.send_request(
                    query,
                    tokens=self.usage_service.count_tokens(query),
                    is_chatgpt_request=True
                    if "turbo" in str(self.model.model)
                    else False,
                )
                welcome_message = str(welcome_message_response["choices"][0]["text"])
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
        self.debug_channel = self.bot.get_guild(self.DEBUG_GUILD).get_channel(
            self.DEBUG_CHANNEL
        )
        print("The debug channel was acquired")

        print("Attempting to load from pickles")
        # Try to load self.full_conversation_history, self.conversation_threads, and self.conversation_thread_owners from the `pickles` folder
        try:
            with open(
                EnvService.save_path() / "pickles" / "full_conversation_history.pickle",
                "rb",
            ) as f:
                self.full_conversation_history = pickle.load(f)
                print("Loaded full_conversation_history")

            with open(
                EnvService.save_path() / "pickles" / "conversation_threads.pickle", "rb"
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

            # Fail if all three weren't loaded
            assert self.full_conversation_history is not {}
            assert self.conversation_threads is not {}
            assert self.conversation_thread_owners is not defaultdict(list)

        except Exception:
            print("Failed to load from pickles")
            self.full_conversation_history = defaultdict(list)
            self.conversation_threads = {}
            self.conversation_thread_owners = defaultdict(list)
            traceback.print_exc()

        print("Syncing commands...")

        await self.bot.sync_commands(
            commands=None,
            method="individual",
            force=True,
            guild_ids=ALLOWED_GUILDS,
            register_guild_commands=True,
            check_guilds=[],
            delete_existing=True,
        )
        print("Commands synced")

        # Start an inline async loop that runs every 10 seconds to save the conversation history to a pickle file
        print("Starting pickle loop")
        while True:
            await asyncio.sleep(15)
            await self.pickle_queue.put(
                Pickler(
                    self.full_conversation_history,
                    self.conversation_threads,
                    self.conversation_thread_owners,
                )
            )

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

    async def end_conversation(
        self, ctx, opener_user_id=None, conversation_limit=False
    ):
        """end the thread of the user interacting with the bot, if the conversation has reached the limit close it for the owner"""
        normalized_user_id = opener_user_id if opener_user_id else ctx.author.id
        # Check if the channel is an instance of a thread
        thread = False
        if isinstance(ctx.channel, discord.Thread):
            thread = True


        if (
            conversation_limit
        ):  # if we reach the conversation limit we want to close from the channel it was maxed out in
            channel_id = ctx.channel.id
        else:
            try:
                channel_ids = self.conversation_thread_owners[normalized_user_id]
                if ctx.channel.id not in channel_ids:
                    await ctx.reply(
                        "This is not a conversation thread that you own!",
                        delete_after=5,
                    )
                    return

                if normalized_user_id in self.awaiting_responses:
                    await ctx.reply(
                        embed=discord.Embed(
                            title=f"Please wait for a response before ending the conversation.",
                            color=0x808080,
                        )
                    )
                    return

            except Exception:
                traceback.print_exc()
                await ctx.delete(delay=5)
                await ctx.reply(
                    "Only the conversation starter can end this.", delete_after=5
                )
                return

        # TODO Possible bug here, if both users have a conversation active and one user tries to end the other, it may
        # allow them to click the end button on the other person's thread and it will end their own convo.
        self.conversation_threads.pop(ctx.channel.id)

        if isinstance(
            ctx, discord.ApplicationContext
        ):  # When the conversation is ended from the slash command
            await ctx.respond(
                "You have ended the conversation with GPT3. Start a conversation with /gpt converse",
                ephemeral=True,
                delete_after=10,
            )
        elif isinstance(
            ctx, discord.Interaction
        ):  # When the user ends the conversation from the button
            await ctx.response.send_message(
                "You have ended the conversation with GPT3. Start a conversation with /gpt converse",
                ephemeral=True,
                delete_after=10,
            )
        else:  # The case for when the user types "end" in the channel
            await ctx.reply(
                "You have ended the conversation with GPT3. Start a conversation with /gpt converse",
                delete_after=10,
            )

        await ctx.channel.send(
            embed=EmbedStatics.generate_end_embed(),
            view=ShareView(self, ctx.channel.id) if thread else None,
        )

        # Close all conversation threads for the user
        # If at conversation limit then fetch the owner and close the thread for them
        if conversation_limit:
            try:
                owner_id = [
                    owner
                    for owner, threads in self.conversation_thread_owners.items()
                    if channel_id in threads
                ][0]
                self.conversation_thread_owners[owner_id].remove(ctx.channel.id)
                # Attempt to close and lock the thread.
                if thread:
                    try:
                        thread = await self.bot.fetch_channel(channel_id)
                        await thread.edit(locked=True)
                        await thread.edit(name="Closed-GPT")
                    except Exception:
                        traceback.print_exc()
            except Exception:
                traceback.print_exc()
        else:
            if normalized_user_id in self.conversation_thread_owners:
                thread_id = ctx.channel.id
                self.conversation_thread_owners[normalized_user_id].remove(
                    ctx.channel.id
                )

                # Attempt to close and lock the thread.
                if thread:
                    try:
                        thread = await self.bot.fetch_channel(thread_id)
                        await thread.edit(locked=True)
                        await thread.edit(name="Closed-GPT")
                    except Exception:
                        traceback.print_exc()

    async def send_settings_text(self, ctx):
        """compose and return the settings menu to the interacting user"""
        embed = discord.Embed(
            title="GPT3Bot Settings",
            description="The current settings of the model",
            color=0x00FF00,
        )
        # Create a two-column embed to display the settings, use \u200b to create a blank space
        embed.add_field(
            name="Setting",
            value="\n".join(
                [
                    key
                    for key in self.model.__dict__.keys()
                    if key not in self.model._hidden_attributes
                ]
            ),
            inline=True,
        )
        embed.add_field(
            name="Value",
            value="\n".join(
                [
                    str(value)
                    for key, value in self.model.__dict__.items()
                    if key not in self.model._hidden_attributes
                ]
            ),
            inline=True,
        )
        await ctx.respond(embed=embed, ephemeral=True)

    async def process_settings(self, ctx, parameter, value):
        """Given a parameter and value set the corresponding parameter in storage to the value"""

        # Check if the parameter is a valid parameter
        if hasattr(self.model, parameter):
            # Check if the value is a valid value
            try:
                # Set the parameter to the value
                setattr(self.model, parameter, value)
                await ctx.respond(
                    "Successfully set the parameter " + parameter + " to " + value
                )

                if parameter == "mode":
                    await ctx.send_followup(
                        "The mode has been set to "
                        + value
                        + ". This has changed the temperature top_p to the mode defaults of "
                        + str(self.model.temp)
                        + " and "
                        + str(self.model.top_p)
                    )
            except ValueError as e:
                await ctx.respond(e)
        else:
            await ctx.respond("The parameter is not a valid parameter")

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

    async def paginate_embed(self, response_text, codex, prompt=None, instruction=None):
        """Given a response text make embed pages and return a list of the pages. Codex makes it a codeblock in the embed"""
        if codex:  # clean codex input
            response_text = response_text.replace("```", "")
            response_text = response_text.replace(f"***Prompt: {prompt}***\n", "")
            response_text = response_text.replace(
                f"***Instruction: {instruction}***\n\n", ""
            )

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
                    description=chunk
                    if not codex
                    else f"***Prompt:{prompt}***\n***Instruction:{instruction:}***\n```python\n{chunk}\n```",
                )
                first = True
            else:
                page = discord.Embed(
                    title=f"Page {count}",
                    description=chunk if not codex else f"```python\n{chunk}\n```",
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
                    "You have reached the maximum conversation length. You have ended the conversation with GPT3, and it has ended."
                )
                await self.end_conversation(message, conversation_limit=True)
                return True
        return False

    async def summarize_conversation(self, message, prompt):
        """Takes a conversation history filled prompt and summarizes it to then start a new history with it as the base"""
        response = await self.model.send_summary_request(prompt)
        summarized_text = response["choices"][0]["text"]

        new_conversation_history = []
        new_conversation_history.append(
            EmbeddedConversationItem(self.CONVERSATION_STARTER_TEXT, 0)
        )
        new_conversation_history.append(
            EmbeddedConversationItem(
                "\nThis conversation has some context from earlier, which has been summarized as follows: ",
                0,
            )
        )
        new_conversation_history.append(EmbeddedConversationItem(summarized_text, 0))
        new_conversation_history.append(
            EmbeddedConversationItem(
                "\nContinue the conversation, paying very close attention to things <username> told you, such as their name, and personal details.\n",
                0,
            )
        )
        # Get the last entry from the thread's conversation history
        new_conversation_history.append(
            EmbeddedConversationItem(
                self.conversation_threads[message.channel.id].history[-1].text + "\n", 0
            )
        )
        self.conversation_threads[message.channel.id].history = new_conversation_history

    # A listener for message edits to redo prompts if they are edited
    @discord.Cog.listener()
    async def on_message_edit(self, before, after):
        """When a message is edited run moderation if enabled, and process if it a prompt that should be redone"""

        if after.author.id == self.bot.user.id:
            return

        # Moderation
        if not isinstance(after.channel, discord.DMChannel):
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

        await TextService.process_conversation_edit(self, after, original_message)

    @discord.Cog.listener()
    async def on_message(self, message):
        """On a new message check if it should be moderated then process it for conversation"""
        if message.author == self.bot.user:
            return

        # Check if the message is a discord system message
        if message.type != discord.MessageType.default:
            return

        # Moderations service is done here.
        if (
            hasattr(message, "guild")
            and message.guild.id in Moderation.moderation_queues
            and Moderation.moderation_queues[message.guild.id] is not None
        ):
            # Don't moderate if there is no "roles" attribute for the author
            if not hasattr(message.author, "roles"):
                pass
            # Verify that the user is not in a role that can bypass moderation
            elif CHAT_BYPASS_ROLES is [None] or not any(
                role.name.lower() in CHAT_BYPASS_ROLES for role in message.author.roles
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

        # Process the message if the user is in a conversation
        if await TextService.process_conversation_message(
            self, message, USER_INPUT_API_KEYS, USER_KEY_DB
        ):
            original_message[message.author.id] = message.id

        # If the user tagged the bot and the tag wasn't an @here or @everyone, retrieve the message
        if f"<@{self.bot.user.id}>" in message.content and not (
            "@everyone" in message.content or "@here" in message.content
        ):
            if not BOT_TAGGABLE:
                return

            # Check if any of the message author's role names are in BOT_TAGGABLE_ROLES, if not, return
            if BOT_TAGGABLE_ROLES != [None] and not any(
                role.name.lower() in BOT_TAGGABLE_ROLES for role in message.author.roles
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
                prompt,
                False,
                None,
                None,
                None,
                None,
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
        if author_id in self.awaiting_responses:
            self.awaiting_responses.remove(author_id)
        if not from_ask_command and not from_edit_command:
            if channel_id in self.awaiting_thread_responses:
                self.awaiting_thread_responses.remove(channel_id)

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
            title="GPT3Bot Help", description="The current commands", color=0xC730C7
        )
        embed.add_field(
            name="/search",
            value="AI-Assisted google search!",
            inline=False,
        )
        embed.add_field(
            name="/index",
            value="Indexing commands for document knowledge and querying",
            inline=False,
        )
        embed.add_field(
            name="/gpt ask",
            value="Ask GPT3 something. Be clear, long, and concise in your prompt. Don't waste tokens.",
            inline=False,
        )
        embed.add_field(
            name="/gpt edit",
            value="Use GPT3 to edit a piece of text given an instruction",
            inline=False,
        )
        embed.add_field(
            name="/gpt converse", value="Start a conversation with GPT3", inline=False
        )
        embed.add_field(
            name="/gpt end",
            value="End a conversation with GPT3. You can also type `end` in the conversation.",
            inline=False,
        )
        embed.add_field(
            name="/dalle draw <image prompt>",
            value="Use DALL-E2 to draw an image based on a text prompt",
            inline=False,
        )
        embed.add_field(
            name="/dalle optimize <image prompt>",
            value="Optimize an image prompt for use with DALL-E2, Midjourney, SD, etc.",
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
        """Command handler. Deletes all threads made by the bot in the current guild"""
        await ctx.defer()

        for thread in ctx.guild.threads:
            thread_name = thread.name.lower()
            if "with gpt" in thread_name or "closed-gpt" in thread_name:
                try:
                    await thread.delete()
                except Exception:
                    pass
        await ctx.respond("All conversation threads in this server have been deleted.")

    async def usage_command(self, ctx):
        """Command handler. Responds with the current usage of the bot"""
        await ctx.defer()
        embed = discord.Embed(
            title="GPT3Bot Usage", description="The current usage", color=0x00FF00
        )
        # 1000 tokens costs 0.02 USD, so we can calculate the total tokens used from the price that we have stored
        embed.add_field(
            name="Total tokens used",
            value=str(int((await self.usage_service.get_usage() / 0.02)) * 1000),
            inline=False,
        )
        embed.add_field(
            name="Total price",
            value="$" + str(round(await self.usage_service.get_usage(), 2)),
            inline=False,
        )
        await ctx.respond(embed=embed)

    async def ask_command(
        self,
        ctx: discord.ApplicationContext,
        prompt: str,
        private: bool,
        temperature: float,
        top_p: float,
        frequency_penalty: float,
        presence_penalty: float,
        from_ask_action=None,
        from_other_action=None,
        from_message_context=None,
        model=None,
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
        prompt = await self.mention_to_username(ctx, prompt.strip())

        if len(prompt) < self.model.prompt_min_length:
            alias = ctx.respond if is_context else ctx.send
            await alias(
                f"Prompt must be greater than {self.model.prompt_min_length} characters, it is currently: {len(prompt)} characters"
            )
            return

        user_api_key = None
        if USER_INPUT_API_KEYS:
            user_api_key = await TextService.get_user_api_key(user.id, ctx, USER_KEY_DB)
            if not user_api_key:
                return

        await ctx.defer(ephemeral=private) if is_context else None

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
        codex: bool,
    ):
        """Command handler. Requests and returns a generation with no extras to the edit endpoint

        Args:
            ctx (discord.ApplicationContext): Command interaction
            instruction (str): The modification instructions
            text (str): The text that should be modified
            temperature (float): Sets the temperature override
            top_p (float): Sets the top p override
            codex (bool): Enables the codex edit model
        """
        user = ctx.user

        text = await self.mention_to_username(ctx, text.strip())
        instruction = await self.mention_to_username(ctx, instruction.strip())

        # Validate that  all the parameters are in a good state before we send the request
        if len(instruction) < self.model.prompt_min_length:
            await ctx.respond(
                f"Instruction must be at least {self.model.prompt_min_length} characters long"
            )
            return

        user_api_key = None
        if USER_INPUT_API_KEYS:
            user_api_key = await TextService.get_user_api_key(user.id, ctx, USER_KEY_DB)
            if not user_api_key:
                return

        await ctx.defer(ephemeral=private)

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
            codex=codex,
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
        opener: str,
        opener_file: str,
        private: bool,
        minimal: bool,
        model: str,
        temperature: float,
        top_p: float,
        frequency_penalty: float,
        presence_penalty: float,
        use_threads: bool = True,  # Add this parameter
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

        user = ctx.user

        # If we are in user input api keys mode, check if the user has entered their api key before letting them continue
        user_api_key = None
        if USER_INPUT_API_KEYS:
            user_api_key = await TextService.get_user_api_key(user.id, ctx, USER_KEY_DB)
            if not user_api_key:
                return

        if private:
            await ctx.defer(ephemeral=True)
        elif not private:
            await ctx.defer()

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
                target = thread
            else:
                embed_title = f"{user.name}'s conversation with GPT"
                message_embed = discord.Embed(title=embed_title, color=0x808080)
                message_thread = await ctx.send(embed=message_embed)
                thread = await message_thread.create_thread(
                    name=user.name + "'s conversation with GPT",
                    auto_archive_duration=60,
                )
                target = thread
        else:
            target = ctx.channel
            if private:
                embed_title = f"{user.name}'s private conversation with GPT"
            else:
                embed_title = f"{user.name}'s conversation with GPT"

            embed = discord.Embed(title=embed_title, color=0x808080)
            await ctx.respond(embed=embed)



        self.conversation_threads[target.id] = Thread(target.id)
        self.conversation_threads[target.id].model = (
            self.model.model if not model else model
        )

        # Set the overrides for the conversation
        self.conversation_threads[target.id].set_overrides(
            temperature, top_p, frequency_penalty, presence_penalty
        )

        if opener:
            opener = await self.mention_to_username(ctx, opener)

        if not opener and not opener_file:
            user_id_normalized = user.id
        else:
            user_id_normalized = ctx.author.id
            if not opener_file:
                pass
            else:
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
                            frequency_penalty = opener_file.get(
                                "frequency_penalty", None
                            )
                            presence_penalty = opener_file.get("presence_penalty", None)
                            self.conversation_threads[target.id].set_overrides(
                                temperature, top_p, frequency_penalty, presence_penalty
                            )
                            if (
                                not opener
                            ):  # if we only use opener_file then only pass on opener_file for the opening prompt
                                opener = opener_file.get("text", "error getting text")
                            else:
                                opener = (
                                    opener_file.get("text", "error getting text")
                                    + opener
                                )
                        except Exception:  # Parse as just regular text
                            if not opener:
                                opener = opener_file
                            else:
                                opener = opener_file + opener
                    except Exception:
                        opener_file = None  # Just start a regular thread if the file fails to load

        # Append the starter text for gpt3 to the user's history so it gets concatenated with the prompt later
        if minimal or opener_file or opener:
            self.conversation_threads[target.id].history.append(
                EmbeddedConversationItem(self.CONVERSATION_STARTER_TEXT_MINIMAL, 0)
            )
        elif not minimal:
            self.conversation_threads[target.id].history.append(
                EmbeddedConversationItem(self.CONVERSATION_STARTER_TEXT, 0)
            )

        # Set user as thread owner before sending anything that can error and leave the thread unowned
        self.conversation_thread_owners[user_id_normalized].append(target.id)
        overrides = self.conversation_threads[target.id].get_overrides()

        await target.send(f"<@{str(ctx.user.id)}> is the thread owner.")

        await target.send(
            embed=EmbedStatics.generate_conversation_embed(
                self.conversation_threads, target, opener, overrides
            )
        )

        # send opening
        if opener:
            target_message = await target.send(
                embed=EmbedStatics.generate_opener_embed(opener)
            )
            if target.id in self.conversation_threads:
                self.awaiting_responses.append(user_id_normalized)
                self.awaiting_target_responses.append(target.id)

                # ... (no other changes in the middle part of the function)

            overrides = Override(
                overrides["temperature"],
                overrides["top_p"],
                overrides["frequency_penalty"],
                overrides["presence_penalty"],
            )

            await TextService.encapsulated_send(
                self,
                target.id,
                opener
                if target.id not in self.conversation_threads or self.pinecone_service
                else "".join(
                    [item.text for item in self.conversation_threads[target.id].history]
                ),
                target_message,
                overrides=overrides,
                user=user,
                model=self.conversation_threads[target.id].model,
                custom_api_key=user_api_key,
            )
            self.awaiting_responses.remove(user_id_normalized)
            if target.id in self.awaiting_target_responses:
                self.awaiting_target_responses.remove(target.id)

    async def end_command(self, ctx: discord.ApplicationContext):
        """Command handler. Gets the user's thread and ends it"""
        await ctx.defer(ephemeral=True)
        user_id = ctx.user.id

        if ctx.channel.id in self.conversation_threads:
            try:
                await self.end_conversation(ctx)
            except Exception as e:
                print(e)
                traceback.print_exc()
        else:
            await ctx.respond(
                "This is not a conversation channel.", ephemeral=True, delete_after=10
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
        prompt = await self.mention_to_username(ctx, message.content)
        await self.ask_command(
            ctx,
            prompt,
            private=False,
            temperature=None,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
            from_ask_action=prompt,
        )

    async def paraphrase_action(self, ctx, message: discord.Message):
        """Message command. paraphrase the current message content"""
        user = ctx.user
        prompt = await self.mention_to_username(ctx, message.content)
        from_other_action = prompt + "\nParaphrased:"

        # Construct the paraphrase prompt
        prompt = f"Paraphrase the following text. Maintain roughly the same text length after paraphrasing and the same tone of voice: {prompt} \nParaphrased:"

        tokens = self.model.usage_service.count_tokens(prompt)
        if tokens > self.model.max_tokens - 1000:
            await ctx.respond(
                f"This message is too long to paraphrase.",
                ephemeral=True,
                delete_after=10,
            )
            return

        await self.ask_command(
            ctx,
            prompt,
            private=False,
            temperature=None,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
            from_other_action=from_other_action,
        )

    async def elaborate_action(self, ctx, message: discord.Message):
        """Message command. elaborate on the subject of the current message content"""
        user = ctx.user
        prompt = await self.mention_to_username(ctx, message.content)
        from_other_action = prompt + "\nElaboration:"

        # Construct the paraphrase prompt
        prompt = f"Elaborate with more information about the subject of the following message. Be objective and detailed and respond with elaborations only about the subject(s) of the message: {prompt} \n\nElaboration:"

        tokens = self.model.usage_service.count_tokens(prompt)
        if tokens > self.model.max_tokens - 500:
            await ctx.respond(
                f"This message is too long to elaborate on.",
                ephemeral=True,
                delete_after=10,
            )
            return

        await self.ask_command(
            ctx,
            prompt=prompt,
            private=False,
            temperature=None,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
            from_other_action=from_other_action,
        )

    async def summarize_action(self, ctx, message: discord.Message):
        """Message command. elaborate on the subject of the current message content"""
        user = ctx.user
        prompt = await self.mention_to_username(ctx, message.content)
        from_other_action = (
            "Message at message link: " + message.jump_url + "\nSummarized:"
        )

        # Construct the paraphrase prompt
        prompt = f"Summarize the following message, be as short and concise as possible: {prompt} \n\nSummary:"

        tokens = self.model.usage_service.count_tokens(prompt)
        if tokens > self.model.max_tokens - 300:
            await ctx.respond(
                f"Your prompt is too long. It has {tokens} tokens, but the maximum is {self.model.max_tokens-300}.",
                ephemeral=True,
                delete_after=10,
            )
            return

        await self.ask_command(
            ctx,
            prompt,
            private=False,
            temperature=None,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
            from_other_action=from_other_action,
        )


class ShareView(discord.ui.View):
    def __init__(
        self,
        converser_cog,
        conversation_id,
    ):
        super().__init__(timeout=3600)  # 1 hour interval to share the conversation.
        self.converser_cog = converser_cog
        self.conversation_id = conversation_id
        self.add_item(ShareButton(converser_cog, conversation_id))

    async def on_timeout(self):
        # Remove the button from the view/message
        self.clear_items()


class ShareButton(discord.ui.Button["ShareView"]):
    def __init__(self, converser_cog, conversation_id):
        super().__init__(
            style=discord.ButtonStyle.green,
            label="Share Conversation",
            custom_id="share_conversation",
        )
        self.converser_cog = converser_cog
        self.conversation_id = conversation_id

    async def callback(self, interaction: discord.Interaction):
        # Get the user
        try:
            id = await self.converser_cog.sharegpt_service.format_and_share(
                self.converser_cog.full_conversation_history[self.conversation_id],
                self.converser_cog.bot.user.default_avatar.url
                if not self.converser_cog.bot.user.avatar
                else self.converser_cog.bot.user.avatar.url,
            )
            url = f"https://shareg.pt/{id}"
            await interaction.response.send_message(
                embed=EmbedStatics.get_conversation_shared_embed(url)
            )
        except ValueError as e:
            traceback.print_exc()
            await interaction.response.send_message(
                embed=EmbedStatics.get_conversation_share_failed_embed(
                    "The ShareGPT API returned an error: " + str(e)
                ),
                ephemeral=True,
                delete_after=15,
            )
            return
        except Exception as e:
            traceback.print_exc()
            await interaction.response.send_message(
                embed=EmbedStatics.get_conversation_share_failed_embed(str(e)),
                ephemeral=True,
                delete_after=15,
            )
            return
