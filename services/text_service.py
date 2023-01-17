import datetime
import re
import traceback

import aiohttp
import discord
from discord.ext import pages

from services.deletion_service import Deletion
from models.openai_model import Model
from models.user_model import EmbeddedConversationItem, RedoUser

CHAT_BYPASS_ROLES = EnvService.get_bypass_roles()

class TextService:
    def __init__(self):
        pass

    @staticmethod
    async def encapsulated_send(
        converser_cog,
        id,
        prompt,
        ctx,
        response_message=None,
        temp_override=None,
        top_p_override=None,
        frequency_penalty_override=None,
        presence_penalty_override=None,
        instruction=None,
        from_ask_command=False,
        from_edit_command=False,
        codex=False,
        model=None,
        custom_api_key=None,
        edited_request=False,
        redo_request=False,
        from_action=False,
    ):
        """General service function for sending and recieving gpt generations

        Args:
            converser_cog (Cog): The conversation cog with our gpt commands
            id (user or thread id): A user or thread id for keeping track of conversations
            prompt (str): The prompt to use for generation
            ctx (ApplicationContext): The interaction which called this
            response_message (discord.Message, optional): For when we're doing redos. Defaults to None.
            temp_override (float, optional): Sets the temperature for the generation. Defaults to None.
            top_p_override (float, optional): Sets the top p for the generation. Defaults to None.
            frequency_penalty_override (float, optional): Sets the frequency penalty for the generation. Defaults to None.
            presence_penalty_override (float, optional): Sets the presence penalty for the generation. Defaults to None.
            instruction (str, optional): Instruction for use with the edit endpoint. Defaults to None.
            from_ask_command (bool, optional): Called from the ask command. Defaults to False.
            from_edit_command (bool, optional): Called from the edit command. Defaults to False.
            codex (bool, optional): Pass along that we want to use a codex model. Defaults to False.
            model (str, optional): Which model to genereate output with. Defaults to None.
            custom_api_key (str, optional): per-user api key. Defaults to None.
            edited_request (bool, optional): If we're doing an edited message. Defaults to False.
            redo_request (bool, optional): If we're redoing a previous prompt. Defaults to False.
            from_action (bool, optional): If the function is being called from a message action. Defaults to False.
        """
        new_prompt = (
            prompt + "\nGPTie: "
            if not from_ask_command and not from_edit_command
            else prompt
        )

        from_context = isinstance(ctx, discord.ApplicationContext)

        if not instruction:
            tokens = converser_cog.usage_service.count_tokens(new_prompt)
        else:
            tokens = converser_cog.usage_service.count_tokens(
                new_prompt
            ) + converser_cog.usage_service.count_tokens(instruction)

        try:

            # Pinecone is enabled, we will create embeddings for this conversation.
            if (
                converser_cog.pinecone_service
                and ctx.channel.id in converser_cog.conversation_threads
            ):
                # Delete "GPTie:  <|endofstatement|>" from the user's conversation history if it exists
                # check if the text attribute for any object inside converser_cog.conversation_threads[converation_id].history
                # contains ""GPTie: <|endofstatement|>"", if so, delete
                for item in converser_cog.conversation_threads[ctx.channel.id].history:
                    if item.text.strip() == "GPTie:<|endofstatement|>":
                        converser_cog.conversation_threads[
                            ctx.channel.id
                        ].history.remove(item)

                # The conversation_id is the id of the thread
                conversation_id = ctx.channel.id

                # Create an embedding and timestamp for the prompt
                new_prompt = prompt.encode("ascii", "ignore").decode()
                prompt_less_author = f"{new_prompt} <|endofstatement|>\n"

                user_displayname = ctx.author.display_name

                new_prompt = (
                    f"\n'{user_displayname}': {new_prompt} <|endofstatement|>\n"
                )
                new_prompt = new_prompt.encode("ascii", "ignore").decode()

                timestamp = int(
                    str(datetime.datetime.now().timestamp()).replace(".", "")
                )

                new_prompt_item = EmbeddedConversationItem(new_prompt, timestamp)

                if not redo_request:
                    converser_cog.conversation_threads[conversation_id].history.append(
                        new_prompt_item
                    )

                if edited_request:
                    new_prompt = "".join(
                        [
                            item.text
                            for item in converser_cog.conversation_threads[
                                ctx.channel.id
                            ].history
                        ]
                    )
                    converser_cog.redo_users[ctx.author.id].prompt = new_prompt
                else:
                    # Create and upsert the embedding for  the conversation id, prompt, timestamp
                    await converser_cog.pinecone_service.upsert_conversation_embedding(
                        converser_cog.model,
                        conversation_id,
                        new_prompt,
                        timestamp,
                        custom_api_key=custom_api_key,
                    )

                    embedding_prompt_less_author = await converser_cog.model.send_embedding_request(
                        prompt_less_author, custom_api_key=custom_api_key
                    )  # Use the version of the prompt without the author's name for better clarity on retrieval.

                    # Now, build the new prompt by getting the X most similar with pinecone
                    similar_prompts = converser_cog.pinecone_service.get_n_similar(
                        conversation_id,
                        embedding_prompt_less_author,
                        n=converser_cog.model.num_conversation_lookback,
                    )

                    # When we are in embeddings mode, only the pre-text is contained in converser_cog.conversation_threads[message.channel.id].history, so we
                    # can use that as a base to build our new prompt
                    prompt_with_history = [
                        converser_cog.conversation_threads[ctx.channel.id].history[0]
                    ]

                    # Append the similar prompts to the prompt with history
                    prompt_with_history += [
                        EmbeddedConversationItem(prompt, timestamp)
                        for prompt, timestamp in similar_prompts
                    ]

                    # iterate UP TO the last X prompts in the history
                    for i in range(
                        1,
                        min(
                            len(
                                converser_cog.conversation_threads[
                                    ctx.channel.id
                                ].history
                            ),
                            converser_cog.model.num_static_conversation_items,
                        ),
                    ):
                        prompt_with_history.append(
                            converser_cog.conversation_threads[ctx.channel.id].history[
                                -i
                            ]
                        )

                    # remove duplicates from prompt_with_history and set the conversation history
                    prompt_with_history = list(dict.fromkeys(prompt_with_history))
                    converser_cog.conversation_threads[
                        ctx.channel.id
                    ].history = prompt_with_history

                    # Sort the prompt_with_history by increasing timestamp if pinecone is enabled
                    if converser_cog.pinecone_service:
                        prompt_with_history.sort(key=lambda x: x.timestamp)

                    # Ensure that the last prompt in this list is the prompt we just sent (new_prompt_item)
                    if prompt_with_history[-1] != new_prompt_item:
                        try:
                            prompt_with_history.remove(new_prompt_item)
                        except ValueError:
                            pass
                        prompt_with_history.append(new_prompt_item)

                    prompt_with_history = "".join(
                        [item.text for item in prompt_with_history]
                    )

                    new_prompt = prompt_with_history + "\nGPTie: "

                tokens = converser_cog.usage_service.count_tokens(new_prompt)

            # No pinecone, we do conversation summarization for long term memory instead
            elif (
                id in converser_cog.conversation_threads
                and tokens > converser_cog.model.summarize_threshold
                and not from_ask_command
                and not from_edit_command
                and not converser_cog.pinecone_service  # This should only happen if we are not doing summarizations.
            ):

                # We don't need to worry about the differences between interactions and messages in this block,
                # because if we are in this block, we can only be using a message object for ctx
                if converser_cog.model.summarize_conversations:
                    await ctx.reply(
                        "I'm currently summarizing our current conversation so we can keep chatting, "
                        "give me one moment!"
                    )

                    await converser_cog.summarize_conversation(ctx, new_prompt)

                    # Check again if the prompt is about to go past the token limit
                    new_prompt = (
                        "".join(
                            [
                                item.text
                                for item in converser_cog.conversation_threads[
                                    id
                                ].history
                            ]
                        )
                        + "\nGPTie: "
                    )

                    tokens = converser_cog.usage_service.count_tokens(new_prompt)

                    if (
                        tokens > converser_cog.model.summarize_threshold - 150
                    ):  # 150 is a buffer for the second stage
                        await ctx.reply(
                            "I tried to summarize our current conversation so we could keep chatting, "
                            "but it still went over the token "
                            "limit. Please try again later."
                        )

                        await converser_cog.end_conversation(ctx)
                        return
                else:
                    await ctx.reply("The conversation context limit has been reached.")
                    await converser_cog.end_conversation(ctx)
                    return

            # Send the request to the model
            if from_edit_command:
                response = await converser_cog.model.send_edit_request(
                    text=new_prompt,
                    instruction=instruction,
                    temp_override=temp_override,
                    top_p_override=top_p_override,
                    codex=codex,
                    custom_api_key=custom_api_key,
                )
            else:
                response = await converser_cog.model.send_request(
                    new_prompt,
                    tokens=tokens,
                    temp_override=temp_override,
                    top_p_override=top_p_override,
                    frequency_penalty_override=frequency_penalty_override,
                    presence_penalty_override=presence_penalty_override,
                    model=model,
                    custom_api_key=custom_api_key,
                )

            # Clean the request response
            response_text = converser_cog.cleanse_response(
                str(response["choices"][0]["text"])
            )

            if from_ask_command or from_action:
                # Append the prompt to the beginning of the response, in italics, then a new line
                response_text = response_text.strip()
                response_text = f"***{prompt}***\n\n{response_text}"
            elif from_edit_command:
                if codex:
                    response_text = response_text.strip()
                    response_text = f"***Prompt: {prompt}***\n***Instruction: {instruction}***\n\n```\n{response_text}\n```"
                else:
                    response_text = response_text.strip()
                    response_text = f"***Prompt: {prompt}***\n***Instruction: {instruction}***\n\n{response_text}\n"

            # If gpt3 tries writing a user mention try to replace it with their name
            response_text = await converser_cog.mention_to_username(ctx, response_text)

            # If the user is conversing, add the GPT response to their conversation history.
            if (
                id in converser_cog.conversation_threads
                and not from_ask_command
                and not converser_cog.pinecone_service
            ):
                if not redo_request:
                    converser_cog.conversation_threads[id].history.append(
                        EmbeddedConversationItem(
                            "\nGPTie: " + str(response_text) + "<|endofstatement|>\n", 0
                        )
                    )

            # Embeddings case!
            elif (
                id in converser_cog.conversation_threads
                and not from_ask_command
                and not from_edit_command
                and converser_cog.pinecone_service
            ):
                conversation_id = id

                # Create an embedding and timestamp for the prompt
                response_text = (
                    "\nGPTie: " + str(response_text) + "<|endofstatement|>\n"
                )

                response_text = response_text.encode("ascii", "ignore").decode()

                # Print the current timestamp
                timestamp = int(
                    str(datetime.datetime.now().timestamp()).replace(".", "")
                )
                converser_cog.conversation_threads[conversation_id].history.append(
                    EmbeddedConversationItem(response_text, timestamp)
                )

                # Create and upsert the embedding for  the conversation id, prompt, timestamp
                embedding = (
                    await converser_cog.pinecone_service.upsert_conversation_embedding(
                        converser_cog.model,
                        conversation_id,
                        response_text,
                        timestamp,
                        custom_api_key=custom_api_key,
                    )
                )

            # Cleanse again
            response_text = converser_cog.cleanse_response(response_text)

            # escape any other mentions like @here or @everyone
            response_text = discord.utils.escape_mentions(response_text)

            # If we don't have a response message, we are not doing a redo, send as a new message(s)
            if not response_message:
                if len(response_text) > converser_cog.TEXT_CUTOFF:
                    if not from_context:
                        paginator = None
                        await converser_cog.paginate_and_send(response_text, ctx)
                    else:
                        embed_pages = await converser_cog.paginate_embed(
                            response_text, codex, prompt, instruction
                        )
                        view = ConversationView(
                            ctx,
                            converser_cog,
                            ctx.channel.id,
                            model,
                            from_ask_command,
                            from_edit_command,
                            custom_api_key=custom_api_key,
                        )
                        paginator = pages.Paginator(
                            pages=embed_pages,
                            timeout=None,
                            custom_view=view,
                            author_check=True,
                        )
                        response_message = await paginator.respond(ctx.interaction)
                else:
                    paginator = None
                    if not from_context:
                        response_message = await ctx.reply(
                            response_text,
                            view=ConversationView(
                                ctx,
                                converser_cog,
                                ctx.channel.id,
                                model,
                                custom_api_key=custom_api_key,
                            ),
                        )
                    elif from_edit_command:
                        response_message = await ctx.respond(
                            response_text,
                            view=ConversationView(
                                ctx,
                                converser_cog,
                                ctx.channel.id,
                                model,
                                from_edit_command=from_edit_command,
                                custom_api_key=custom_api_key,
                            ),
                        )
                    else:
                        response_message = await ctx.respond(
                            response_text,
                            view=ConversationView(
                                ctx,
                                converser_cog,
                                ctx.channel.id,
                                model,
                                from_ask_command=from_ask_command,
                                custom_api_key=custom_api_key,
                            ),
                        )

                if response_message:
                    # Get the actual message object of response_message in case it's an WebhookMessage
                    actual_response_message = (
                        response_message
                        if not from_context
                        else await ctx.fetch_message(response_message.id)
                    )

                    converser_cog.redo_users[ctx.author.id] = RedoUser(
                        prompt=new_prompt,
                        instruction=instruction,
                        ctx=ctx,
                        message=ctx,
                        response=actual_response_message,
                        codex=codex,
                        paginator=paginator,
                    )
                    converser_cog.redo_users[ctx.author.id].add_interaction(
                        actual_response_message.id
                    )

            # We are doing a redo, edit the message.
            else:
                paginator = converser_cog.redo_users.get(ctx.author.id).paginator
                if isinstance(paginator, pages.Paginator):
                    embed_pages = await converser_cog.paginate_embed(
                        response_text, codex, prompt, instruction
                    )
                    view = ConversationView(
                        ctx,
                        converser_cog,
                        ctx.channel.id,
                        model,
                        from_ask_command,
                        from_edit_command,
                        custom_api_key=custom_api_key,
                    )
                    await paginator.update(pages=embed_pages, custom_view=view)
                elif len(response_text) > converser_cog.TEXT_CUTOFF:
                    if not from_context:
                        await response_message.channel.send(
                            "Over 2000 characters", delete_after=5
                        )
                else:
                    await response_message.edit(content=response_text)

            await converser_cog.send_debug_message(
                converser_cog.generate_debug_message(prompt, response),
                converser_cog.debug_channel,
            )

            converser_cog.remove_awaiting(
                ctx.author.id, ctx.channel.id, from_ask_command, from_edit_command
            )

        # Error catching for AIOHTTP Errors
        except aiohttp.ClientResponseError as e:
            message = (
                f"The API returned an invalid response: **{e.status}: {e.message}**"
            )
            if from_context:
                await ctx.send_followup(message)
            else:
                await ctx.reply(message)
            converser_cog.remove_awaiting(
                ctx.author.id, ctx.channel.id, from_ask_command, from_edit_command
            )

        # Error catching for OpenAI model value errors
        except ValueError as e:
            if from_action:
                await ctx.respond(e, ephemeral=True)
            elif from_context:
                await ctx.send_followup(e, ephemeral=True)
            else:
                await ctx.reply(e)
            converser_cog.remove_awaiting(
                ctx.author.id, ctx.channel.id, from_ask_command, from_edit_command
            )

        # General catch case for everything
        except Exception:

            message = "Something went wrong, please try again later. This may be due to upstream issues on the API, or rate limiting."
            if not from_context:
                await ctx.send_followup(message)
            else:
                await ctx.reply(message)

            converser_cog.remove_awaiting(
                ctx.author.id, ctx.channel.id, from_ask_command, from_edit_command
            )
            traceback.print_exc()

            try:
                await converser_cog.end_conversation(ctx)
            except Exception:
                pass
            return

    @staticmethod
    async def process_conversation_message(
        converser_cog, message, USER_INPUT_API_KEYS, USER_KEY_DB
    ):
        content = message.content.strip()
        conversing = converser_cog.check_conversing(message.channel.id, content)

        # If the user is conversing and they want to end it, end it immediately before we continue any further.
        if conversing and message.content.lower() in converser_cog.END_PROMPTS:
            await converser_cog.end_conversation(message)
            return

        if conversing:
            user_api_key = None
            if USER_INPUT_API_KEYS:
                user_api_key = await TextService.get_user_api_key(
                    message.author.id, message, USER_KEY_DB
                )
                if not user_api_key:
                    return

            prompt = await converser_cog.mention_to_username(message, content)

            await converser_cog.check_conversation_limit(message)

            # If the user is in a conversation thread
            if message.channel.id in converser_cog.conversation_threads:

                # Since this is async, we don't want to allow the user to send another prompt while a conversation
                # prompt is processing, that'll mess up the conversation history!
                if message.author.id in converser_cog.awaiting_responses:
                    message = await message.reply(
                        "You are already waiting for a response from GPT3. Please wait for it to respond before sending another message."
                    )

                    # get the current date, add 10 seconds to it, and then turn it into a timestamp.
                    # we need to use our deletion service because this isn't an interaction, it's a regular message.
                    deletion_time = datetime.datetime.now() + datetime.timedelta(
                        seconds=10
                    )
                    deletion_time = deletion_time.timestamp()

                    deletion_message = Deletion(message, deletion_time)
                    await converser_cog.deletion_queue.put(deletion_message)

                    return

                if message.channel.id in converser_cog.awaiting_thread_responses:
                    message = await message.reply(
                        "This thread is already waiting for a response from GPT3. Please wait for it to respond before sending another message."
                    )

                    # get the current date, add 10 seconds to it, and then turn it into a timestamp.
                    # we need to use our deletion service because this isn't an interaction, it's a regular message.
                    deletion_time = datetime.datetime.now() + datetime.timedelta(
                        seconds=10
                    )
                    deletion_time = deletion_time.timestamp()

                    deletion_message = Deletion(message, deletion_time)
                    await converser_cog.deletion_queue.put(deletion_message)

                    return

                converser_cog.awaiting_responses.append(message.author.id)
                converser_cog.awaiting_thread_responses.append(message.channel.id)

                if not converser_cog.pinecone_service:
                    converser_cog.conversation_threads[
                        message.channel.id
                    ].history.append(
                        EmbeddedConversationItem(
                            f"\n'{message.author.display_name}': {prompt} <|endofstatement|>\n",
                            0,
                        )
                    )

                # increment the conversation counter for the user
                converser_cog.conversation_threads[message.channel.id].count += 1

            # Send the request to the model
            # If conversing, the prompt to send is the history, otherwise, it's just the prompt
            if (
                converser_cog.pinecone_service
                or message.channel.id not in converser_cog.conversation_threads
            ):
                primary_prompt = prompt
            else:
                primary_prompt = "".join(
                    [
                        item.text
                        for item in converser_cog.conversation_threads[
                            message.channel.id
                        ].history
                    ]
                )

            # set conversation overrides
            overrides = converser_cog.conversation_threads[
                message.channel.id
            ].get_overrides()

            await TextService.encapsulated_send(
                converser_cog,
                message.channel.id,
                primary_prompt,
                message,
                temp_override=overrides["temperature"],
                top_p_override=overrides["top_p"],
                frequency_penalty_override=overrides["frequency_penalty"],
                presence_penalty_override=overrides["presence_penalty"],
                model=converser_cog.conversation_threads[message.channel.id].model,
                custom_api_key=user_api_key,
            )
            return True

    @staticmethod
    async def get_user_api_key(user_id, ctx, USER_KEY_DB):
        user_api_key = None if user_id not in USER_KEY_DB else USER_KEY_DB[user_id]
        if user_api_key is None or user_api_key == "":
            modal = SetupModal(user_key_db=USER_KEY_DB)
            if isinstance(ctx, discord.ApplicationContext):
                await ctx.send_modal(modal)
                await ctx.send_followup(
                    "You must set up your API key before using this command."
                )
            else:
                await ctx.reply(
                    "You must set up your API key before typing in a GPT3 powered channel, type `/setup` to enter your API key."
                )
        return user_api_key

    @staticmethod
    async def process_conversation_edit(converser_cog, after, original_message):
        if after.author.id in converser_cog.redo_users:
            if after.id == original_message[after.author.id]:
                response_message = converser_cog.redo_users[after.author.id].response
                ctx = converser_cog.redo_users[after.author.id].ctx
                await response_message.edit(content="Redoing prompt 🔄...")

                edited_content = await converser_cog.mention_to_username(
                    after, after.content
                )

                if after.channel.id in converser_cog.conversation_threads:
                    # Remove the last two elements from the history array and add the new <username>: prompt
                    converser_cog.conversation_threads[
                        after.channel.id
                    ].history = converser_cog.conversation_threads[
                        after.channel.id
                    ].history[
                        :-2
                    ]

                    pinecone_dont_reinsert = None
                    if not converser_cog.pinecone_service:
                        converser_cog.conversation_threads[
                            after.channel.id
                        ].history.append(
                            EmbeddedConversationItem(
                                f"\n{after.author.display_name}: {after.content}<|endofstatement|>\n",
                                0,
                            )
                        )

                    converser_cog.conversation_threads[after.channel.id].count += 1

                overrides = converser_cog.conversation_threads[
                    after.channel.id
                ].get_overrides()

                await TextService.encapsulated_send(
                    converser_cog,
                    id=after.channel.id,
                    prompt=edited_content,
                    ctx=ctx,
                    response_message=response_message,
                    temp_override=overrides["temperature"],
                    top_p_override=overrides["top_p"],
                    frequency_penalty_override=overrides["frequency_penalty"],
                    presence_penalty_override=overrides["presence_penalty"],
                    model=converser_cog.conversation_threads[after.channel.id].model,
                    edited_request=True,
                )

                if not converser_cog.pinecone_service:
                    converser_cog.redo_users[after.author.id].prompt = edited_content


#
# Conversation interaction buttons
#


class ConversationView(discord.ui.View):
    def __init__(
        self,
        ctx,
        converser_cog,
        id,
        model,
        from_ask_command=False,
        from_edit_command=False,
        custom_api_key=None,
    ):
        super().__init__(timeout=3600)  # 1 hour interval to redo.
        self.converser_cog = converser_cog
        self.ctx = ctx
        self.model = model
        self.from_ask_command = from_ask_command
        self.from_edit_command = from_edit_command
        self.custom_api_key = custom_api_key
        self.add_item(
            RedoButton(
                self.converser_cog,
                model=model,
                from_ask_command=from_ask_command,
                from_edit_command=from_edit_command,
                custom_api_key=self.custom_api_key,
            )
        )

        if id in self.converser_cog.conversation_threads:
            self.add_item(EndConvoButton(self.converser_cog))

    async def on_timeout(self):
        # Remove the button from the view/message
        self.clear_items()
        # Send a message to the user saying the view has timed out
        if self.message:
            await self.message.edit(
                view=None,
            )
        else:
            await self.ctx.edit(
                view=None,
            )


class EndConvoButton(discord.ui.Button["ConversationView"]):
    def __init__(self, converser_cog):
        super().__init__(
            style=discord.ButtonStyle.danger,
            label="End Conversation",
            custom_id="conversation_end",
        )
        self.converser_cog = converser_cog

    async def callback(self, interaction: discord.Interaction):

        # Get the user
        user_id = interaction.user.id
        if (
            user_id in self.converser_cog.conversation_thread_owners
            and self.converser_cog.conversation_thread_owners[user_id]
            == interaction.channel.id
        ):
            try:
                await self.converser_cog.end_conversation(
                    interaction, opener_user_id=interaction.user.id
                )
            except Exception as e:
                print(e)
                traceback.print_exc()
                await interaction.response.send_message(
                    e, ephemeral=True, delete_after=30
                )
        else:
            await interaction.response.send_message(
                "This is not your conversation to end!", ephemeral=True, delete_after=10
            )


class RedoButton(discord.ui.Button["ConversationView"]):
    def __init__(
        self, converser_cog, model, from_ask_command, from_edit_command, custom_api_key
    ):
        super().__init__(
            style=discord.ButtonStyle.danger,
            label="Retry",
            custom_id="conversation_redo",
        )
        self.converser_cog = converser_cog
        self.model = model
        self.from_ask_command = from_ask_command
        self.from_edit_command = from_edit_command
        self.custom_api_key = custom_api_key

    async def callback(self, interaction: discord.Interaction):

        # Get the user
        user_id = interaction.user.id
        if user_id in self.converser_cog.redo_users and self.converser_cog.redo_users[
            user_id
        ].in_interaction(interaction.message.id):
            # Get the message and the prompt and call encapsulated_send
            prompt = self.converser_cog.redo_users[user_id].prompt
            instruction = self.converser_cog.redo_users[user_id].instruction
            ctx = self.converser_cog.redo_users[user_id].ctx
            response_message = self.converser_cog.redo_users[user_id].response
            codex = self.converser_cog.redo_users[user_id].codex

            await interaction.response.send_message(
                "Retrying your original request...", ephemeral=True, delete_after=15
            )

            await TextService.encapsulated_send(
                self.converser_cog,
                id=user_id,
                prompt=prompt,
                instruction=instruction,
                ctx=ctx,
                model=self.model,
                response_message=response_message,
                codex=codex,
                custom_api_key=self.custom_api_key,
                redo_request=True,
                from_ask_command=self.from_ask_command,
                from_edit_command=self.from_edit_command,
            )
        else:
            await interaction.response.send_message(
                "You can only redo the most recent prompt that you sent yourself.",
                ephemeral=True,
                delete_after=10,
            )


#
# The setup modal when using user input API keys
#


class SetupModal(discord.ui.Modal):
    def __init__(self, user_key_db) -> None:
        super().__init__(title="API Key Setup")
        # Get the argument named "user_key_db" and save it as USER_KEY_DB
        self.USER_KEY_DB = user_key_db

        self.add_item(
            discord.ui.InputText(
                label="OpenAI API Key",
                placeholder="sk--......",
            )
        )

    async def callback(self, interaction: discord.Interaction):
        user = interaction.user
        api_key = self.children[0].value
        # Validate that api_key is indeed in this format
        if not re.match(r"sk-[a-zA-Z0-9]{32}", api_key):
            await interaction.response.send_message(
                "Your API key looks invalid, please check that it is correct before proceeding. Please run the /setup command to set your key.",
                ephemeral=True,
                delete_after=100,
            )
        else:
            # We can save the key for the user to the database.

            # Make a test request using the api key to ensure that it is valid.
            try:
                await Model.send_test_request(api_key)
                await interaction.response.send_message(
                    "Your API key was successfully validated.",
                    ephemeral=True,
                    delete_after=10,
                )

            except aiohttp.ClientResponseError as e:
                await interaction.response.send_message(
                    f"The API returned an invalid response: **{e.status}: {e.message}**",
                    ephemeral=True,
                    delete_after=30,
                )
                return

            except Exception as e:
                await interaction.response.send_message(
                    f"Your API key looks invalid, the API returned: {e}. Please check that your API key is correct before proceeding",
                    ephemeral=True,
                    delete_after=30,
                )
                return

            # Save the key to the database
            try:
                self.USER_KEY_DB[user.id] = api_key
                self.USER_KEY_DB.commit()
                await interaction.followup.send(
                    "Your API key was successfully saved.",
                    ephemeral=True,
                    delete_after=10,
                )
            except Exception:
                traceback.print_exc()
                await interaction.followup.send(
                    "There was an error saving your API key.",
                    ephemeral=True,
                    delete_after=30,
                )
                return
