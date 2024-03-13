import asyncio.exceptions
import datetime
import json
import re
import traceback
from collections import defaultdict

import aiofiles
import aiohttp
import discord
import requests
from discord.ext import pages
import unidecode

from models.embed_statics_model import EmbedStatics
from models.image_understanding_model import ImageUnderstandingModel
from services.deletion_service import Deletion
from models.openai_model import Model, Override, Models
from models.user_model import EmbeddedConversationItem, RedoUser
from services.environment_service import EnvService
from services.moderations_service import Moderation

BOT_NAME = EnvService.get_custom_bot_name()
PRE_MODERATE = EnvService.get_premoderate()
image_understanding_model = ImageUnderstandingModel()


class TextService:
    def __init__(self):
        pass

    @staticmethod
    async def trigger_thinking(message: discord.Message, is_drawing=None):
        thinking_embed = discord.Embed(
            title=f"🤖💬 Thinking..." if not is_drawing else f"🤖🎨 Drawing...",
            color=0x808080,
        )

        thinking_embed.set_footer(text="This may take a few seconds.")
        try:
            thinking_message = await message.reply(embed=thinking_embed)
        except:
            thinking_message = None

        try:
            await message.channel.trigger_typing()
        except Exception:
            thinking_message = None

        return thinking_message

    @staticmethod
    async def stop_thinking(thinking_message: discord.Message):
        try:
            await thinking_message.delete()
        except:
            pass

    @staticmethod
    async def encapsulated_send(
        converser_cog,
        id,
        prompt,
        ctx,
        response_message=None,
        overrides=None,
        instruction=None,
        from_ask_command=False,
        from_edit_command=False,
        model=None,
        user=None,
        custom_api_key=None,
        edited_request=False,
        redo_request=False,
        from_ask_action=False,
        from_other_action=None,
        from_message_context=None,
        is_drawable=False,
    ):
        """General service function for sending and receiving gpt generations

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
            model (str, optional): Which model to generate output with. Defaults to None.
            user (discord.User, optional): An user object that can be used to set the stop. Defaults to None.
            custom_api_key (str, optional): per-user api key. Defaults to None.
            edited_request (bool, optional): If we're doing an edited message. Defaults to False.
            redo_request (bool, optional): If we're redoing a previous prompt. Defaults to False.
            from_action (bool, optional): If the function is being called from a message action. Defaults to False.
        """
        new_prompt, _new_prompt_clean = (
            prompt  # + "\n" + BOT_NAME
            if not from_ask_command and not from_edit_command and not redo_request
            else prompt
        ), prompt

        stop = f"{ctx.author.display_name if user is None else user.display_name}:"

        from_context = isinstance(ctx, discord.ApplicationContext)

        if not instruction:
            tokens = converser_cog.usage_service.count_tokens(new_prompt)
        else:
            tokens = converser_cog.usage_service.count_tokens(
                new_prompt
            ) + converser_cog.usage_service.count_tokens(instruction)

        try:
            user_displayname = (
                ctx.author.display_name if not user else user.display_name
            )

            # Pinecone is enabled, we will create embeddings for this conversation.
            if (
                converser_cog.pinecone_service
                and ctx.channel.id in converser_cog.conversation_threads
            ):
                for item in converser_cog.conversation_threads[ctx.channel.id].history:
                    if item.text.strip() == BOT_NAME + "<|endofstatement|>":
                        converser_cog.conversation_threads[
                            ctx.channel.id
                        ].history.remove(item)

                # The conversation_id is the id of the thread
                conversation_id = ctx.channel.id

                # Create an embedding and timestamp for the prompt
                # new_prompt = prompt.encode("ascii", "ignore").decode()
                new_prompt = unidecode.unidecode(new_prompt)
                prompt_less_author = f"{new_prompt} <|endofstatement|>\n"

                new_prompt = f"\n{user_displayname}: {new_prompt} <|endofstatement|>\n"

                # new_prompt = new_prompt.encode("ascii", "ignore").decode()
                new_prompt = unidecode.unidecode(new_prompt)

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
                    # Print all phrases

                    embedding_prompt_less_author = await converser_cog.model.send_embedding_request(
                        prompt_less_author, custom_api_key=custom_api_key
                    )  # Use the version of the prompt without the author's name for better clarity on retrieval.

                    # Now, build the new prompt by getting the X most similar with pinecone
                    similar_prompts = converser_cog.pinecone_service.get_n_similar(
                        conversation_id,
                        embedding_prompt_less_author,
                        n=converser_cog.model.num_conversation_lookback,
                    )

                    # We use the pretext to build our new history
                    _prompt_with_history = [
                        converser_cog.conversation_threads[ctx.channel.id].history[0]
                    ]

                    # If there's an opener we add it to the history
                    if converser_cog.conversation_threads[ctx.channel.id].has_opener:
                        _prompt_with_history += [
                            converser_cog.conversation_threads[ctx.channel.id].history[
                                1
                            ]
                        ]

                    # Append the similar prompts to the prompt with history
                    _prompt_with_history += [
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
                        _prompt_with_history.append(
                            converser_cog.conversation_threads[ctx.channel.id].history[
                                -i
                            ]
                        )

                    # remove duplicates from prompt_with_history and set the conversation history
                    _prompt_with_history = list(dict.fromkeys(_prompt_with_history))

                    # Sort the prompt_with_history by increasing timestamp if pinecone is enabled
                    if converser_cog.pinecone_service:
                        _prompt_with_history.sort(key=lambda x: x.timestamp)

                    # Remove the last two entries after sort, this is from the end of the list as prompt(redo), answer, prompt(original), leaving only prompt(original) and further history
                    if redo_request:
                        _prompt_with_history = _prompt_with_history[:-2]

                    converser_cog.conversation_threads[ctx.channel.id].history = (
                        _prompt_with_history
                    )

                    # Ensure that the last prompt in this list is the prompt we just sent (new_prompt_item)
                    if _prompt_with_history[-1].text != new_prompt_item.text:
                        try:
                            _prompt_with_history.remove(new_prompt_item)
                        except ValueError:
                            pass
                        _prompt_with_history.append(new_prompt_item)

                    prompt_with_history = "".join(
                        [item.text for item in _prompt_with_history]
                    )

                    new_prompt = prompt_with_history + "\n" + BOT_NAME

                tokens = converser_cog.usage_service.count_tokens(new_prompt)

            # No pinecone, we do conversation summarization for long term memory instead
            elif (
                id in converser_cog.conversation_threads
                and tokens > converser_cog.model.summarize_threshold
                and not from_ask_command
                and not from_edit_command
                and not converser_cog.pinecone_service
                # This should only happen if we are not doing summarizations.
            ):
                # We don't need to worry about the differences between interactions and messages in this block,
                # because if we are in this block, we can only be using a message object for ctx
                if converser_cog.model.summarize_conversations:
                    summarizing_message = await ctx.reply(
                        "I'm currently summarizing our current conversation so we can keep chatting, "
                        "give me one moment!"
                    )

                    await converser_cog.summarize_conversation(ctx, new_prompt)

                    try:
                        await summarizing_message.delete()
                    except:
                        pass

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
                        + "\n"
                        + BOT_NAME
                    )

                    tokens = converser_cog.usage_service.count_tokens(new_prompt)

                    if (
                        tokens > converser_cog.model.summarize_threshold
                    ):  # 150 is a buffer for the second stage
                        await ctx.reply(
                            "I tried to summarize our current conversation so we could keep chatting, "
                            "but it still went over the token "
                            "limit. Please try again later."
                        )

                        await converser_cog.end_conversation(ctx)
                        converser_cog.remove_awaiting(
                            ctx.author.id, ctx.channel.id, False, False
                        )
                        return
                else:
                    await ctx.reply("The conversation context limit has been reached.")
                    await converser_cog.end_conversation(ctx)
                    return

            # Send the request to the model
            is_chatgpt_conversation = (
                ctx.channel.id in converser_cog.conversation_threads
                and not from_ask_command
                and not from_edit_command
                and (
                    (
                        model is not None
                        and (
                            model in Models.CHATGPT_MODELS
                            or (model == "chatgpt" or "gpt-4" in model)
                        )
                    )
                    or (
                        model is None
                        and converser_cog.model.model in Models.CHATGPT_MODELS
                    )
                )
            )
            delegator = model or converser_cog.model.model
            is_chatgpt_request = (
                delegator in Models.CHATGPT_MODELS or delegator in Models.GPT4_MODELS
            )

            # Set some variables if a user or channel has a system instruction set
            if ctx.author.id in converser_cog.instructions:
                system_instruction = converser_cog.instructions[ctx.author.id].prompt
                usage_message = "***Added user instruction to prompt***"
                tokens += converser_cog.usage_service.count_tokens(system_instruction)
            elif ctx.channel.id in converser_cog.instructions:
                system_instruction = converser_cog.instructions[ctx.channel.id].prompt
                usage_message = "***Added channel instruction to prompt***"
                tokens += converser_cog.usage_service.count_tokens(system_instruction)
            else:
                system_instruction = None
                usage_message = None

            if is_chatgpt_conversation:
                _prompt_with_history = converser_cog.conversation_threads[
                    ctx.channel.id
                ].history
                response = await converser_cog.model.send_chatgpt_chat_request(
                    _prompt_with_history,
                    model=model,
                    bot_name=BOT_NAME,
                    user_displayname=user_displayname,
                    temp_override=overrides.temperature,
                    top_p_override=overrides.top_p,
                    frequency_penalty_override=overrides.frequency_penalty,
                    presence_penalty_override=overrides.presence_penalty,
                    stop=stop if not from_ask_command else None,
                    custom_api_key=custom_api_key,
                )

            elif from_edit_command:
                response = await converser_cog.model.send_edit_request(
                    text=new_prompt,
                    instruction=instruction,
                    temp_override=overrides.temperature,
                    top_p_override=overrides.top_p,
                    custom_api_key=custom_api_key,
                )
            else:
                response = await converser_cog.model.send_request(
                    new_prompt,
                    tokens=tokens,
                    temp_override=overrides.temperature,
                    top_p_override=overrides.top_p,
                    frequency_penalty_override=overrides.frequency_penalty,
                    presence_penalty_override=overrides.presence_penalty,
                    model=model,
                    stop=stop if not from_ask_command else None,
                    custom_api_key=custom_api_key,
                    is_chatgpt_request=is_chatgpt_request,
                    system_instruction=system_instruction,
                )

            # Clean the request response

            response_text = (
                converser_cog.cleanse_response(str(response["choices"][0]["text"]))
                if not is_chatgpt_request
                and not is_chatgpt_conversation
                or from_edit_command
                else converser_cog.cleanse_response(
                    str(response["choices"][0]["message"]["content"])
                )
            )

            if from_message_context:
                response_text = f"{response_text}"
                response_text = (
                    f"{usage_message}\n\n{response_text}"
                    if system_instruction
                    else response_text
                )
            elif from_other_action:
                response_text = f"***{from_other_action}*** {response_text}"
                response_text = (
                    f"{usage_message}\n\n{response_text}"
                    if system_instruction
                    else response_text
                )
            elif from_ask_command or from_ask_action:
                response_model = response["model"]
                if "gpt-3.5" in response_model or "gpt-4" in response_model:
                    response_text = (
                        f"\n\n{response_text}"
                        if not response_text.startswith("\n\n")
                        else response_text
                    )
                response_text = f"***{prompt}***{response_text}"
                response_text = (
                    f"{usage_message}\n\n{response_text}"
                    if system_instruction
                    else response_text
                )
            elif from_edit_command:
                response_text = response_text.strip()
                response_text = f"***Prompt:***\n {prompt}\n\n***Instruction:***\n {instruction}\n\n***Response:***\n {response_text}"

            # If gpt tries writing a user mention try to replace it with their name
            response_text = await converser_cog.mention_to_username(ctx, response_text)

            # If the user is conversing, add the GPT response to their conversation history.
            if (
                ctx.channel.id in converser_cog.conversation_threads
                and not from_ask_command
                and not converser_cog.pinecone_service
            ):
                if not redo_request:
                    converser_cog.conversation_threads[ctx.channel.id].history.append(
                        EmbeddedConversationItem(
                            "\n"
                            + BOT_NAME
                            + str(response_text)
                            + "<|endofstatement|>\n",
                            0,
                        )
                    )

            # Embeddings case!
            elif (
                ctx.channel.id in converser_cog.conversation_threads
                and not from_ask_command
                and not from_edit_command
                and converser_cog.pinecone_service
            ):
                conversation_id = ctx.channel.id

                # A cleaner version for the convo history
                response_text_clean = str(response_text)

                # Create an embedding and timestamp for the prompt
                response_text = (
                    "\n" + BOT_NAME + str(response_text) + "<|endofstatement|>\n"
                )

                # response_text = response_text.encode("ascii", "ignore").decode()
                response_text = unidecode.unidecode(response_text)

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

            converser_cog.full_conversation_history[ctx.channel.id].append(
                response_text
            )

            # escape any other mentions like @here or @everyone
            response_text = discord.utils.escape_mentions(response_text)

            # If we don't have a response message, we are not doing a redo, send as a new message(s)
            if not response_message:
                if len(response_text) > converser_cog.TEXT_CUTOFF:
                    if not from_context:
                        paginator = None
                        response_message = await converser_cog.paginate_and_send(
                            response_text, ctx
                        )
                    else:
                        embed_pages = await converser_cog.paginate_embed(response_text)
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
                        try:
                            response_message = await paginator.respond(ctx.interaction)
                        except:
                            response_message = await paginator.send(ctx.channel)
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
                            embed=EmbedStatics.get_edit_command_output_embed(
                                response_text
                            ),
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
                converser_cog.redo_users[ctx.author.id] = RedoUser(
                    prompt=new_prompt if not converser_cog.pinecone_service else prompt,
                    instruction=instruction,
                    ctx=ctx,
                    message=ctx,
                    response=response_message,
                    paginator=paginator,
                )
                converser_cog.redo_users[ctx.author.id].add_interaction(
                    response_message.id
                )

            # We are doing a redo, edit the message.
            else:
                paginator = converser_cog.redo_users.get(ctx.author.id).paginator
                if isinstance(paginator, pages.Paginator):
                    embed_pages = await converser_cog.paginate_embed(response_text)
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
                    if not from_edit_command:
                        await response_message.edit(content=response_text)
                    else:
                        await response_message.edit(
                            embed=EmbedStatics.get_edit_command_output_embed(
                                response_text
                            )
                        )

            await converser_cog.send_debug_message(
                converser_cog.generate_debug_message(prompt, response),
                converser_cog.debug_channel,
            )

            converser_cog.remove_awaiting(
                ctx.author.id, ctx.channel.id, from_ask_command, from_edit_command
            )

        # Error catching for AIOHTTP Errors
        except aiohttp.ClientResponseError as e:
            embed = EmbedStatics.get_invalid_api_response_embed(e)
            if from_context:
                await ctx.send_followup(embed=embed)
            else:
                await ctx.reply(embed=embed)
            converser_cog.remove_awaiting(
                ctx.author.id, ctx.channel.id, from_ask_command, from_edit_command
            )

        except asyncio.exceptions.TimeoutError as e:
            embed = EmbedStatics.get_api_timeout_embed()
            if from_context:
                await ctx.send_followup(embed=embed)
            else:
                await ctx.reply(embed=embed)
            converser_cog.remove_awaiting(
                ctx.author.id, ctx.channel.id, from_ask_command, from_edit_command
            )

        # Error catching for OpenAI model value errors
        except ValueError as e:
            embed = EmbedStatics.get_invalid_value_embed(e)
            if from_ask_action:
                await ctx.respond(embed=embed, ephemeral=True)
            elif from_context:
                await ctx.send_followup(embed=embed, ephemeral=True)
            else:
                await ctx.reply(e)
            converser_cog.remove_awaiting(
                ctx.author.id, ctx.channel.id, from_ask_command, from_edit_command
            )

        # General catch case for everything
        except Exception as e:
            embed = EmbedStatics.get_general_error_embed(e)

            try:
                await ctx.channel.send(embed=embed)
            except:
                pass

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
        converser_cog,
        message,
        USER_INPUT_API_KEYS,
        USER_KEY_DB,
        files=None,
        amended_message=None,
    ):
        content = (
            message.content.strip() if not amended_message else amended_message.strip()
        )
        conversing = converser_cog.check_conversing(message.channel.id, content)

        # If the user is conversing and they want to end it, end it immediately before we continue any further.
        if conversing and message.content.lower() in converser_cog.END_PROMPTS:
            await converser_cog.end_conversation(message)
            return

        if conversing:
            # Pre-moderation check
            if PRE_MODERATE:
                if await Moderation.simple_moderate_and_respond(
                    message.content, message
                ):
                    await message.delete()
                    return

            user_api_key = None
            if USER_INPUT_API_KEYS:
                user_api_key = await TextService.get_user_api_key(
                    message.author.id, message, USER_KEY_DB
                )
                if not user_api_key:
                    return

            prompt = await converser_cog.mention_to_username(message, content)

            if await converser_cog.check_conversation_limit(message):
                return

            # If the user is in a conversation thread
            if message.channel.id in converser_cog.conversation_threads:
                # Since this is async, we don't want to allow the user to send another prompt while a conversation
                # prompt is processing, that'll mess up the conversation history!
                if message.author.id in converser_cog.awaiting_responses:
                    resp_message = await message.reply(
                        embed=discord.Embed(
                            title=f"You are already waiting for a response, please wait and speak afterwards.",
                            color=0x808080,
                        )
                    )
                    try:
                        await resp_message.channel.trigger_typing()
                    except:
                        pass

                    # get the current date, add 10 seconds to it, and then turn it into a timestamp.
                    # we need to use our deletion service because this isn't an interaction, it's a regular message.
                    deletion_time = datetime.datetime.now() + datetime.timedelta(
                        seconds=5
                    )
                    deletion_time = deletion_time.timestamp()

                    deletion_message = Deletion(resp_message, deletion_time)
                    deletion_original_message = Deletion(message, deletion_time)
                    await converser_cog.deletion_queue.put(deletion_message)
                    await converser_cog.deletion_queue.put(deletion_original_message)

                    return

                if message.channel.id in converser_cog.awaiting_thread_responses:
                    resp_message = await message.reply(
                        embed=discord.Embed(
                            title=f"This thread is already waiting for a response, please wait and speak afterwards.",
                            color=0x808080,
                        )
                    )
                    try:
                        await resp_message.channel.trigger_typing()
                    except:
                        pass

                    # get the current date, add 10 seconds to it, and then turn it into a timestamp.
                    # we need to use our deletion service because this isn't an interaction, it's a regular message.
                    deletion_time = datetime.datetime.now() + datetime.timedelta(
                        seconds=5
                    )
                    deletion_time = deletion_time.timestamp()

                    deletion_message = Deletion(resp_message, deletion_time)
                    deletion_original_message = Deletion(message, deletion_time)
                    await converser_cog.deletion_queue.put(deletion_message)
                    await converser_cog.deletion_queue.put(deletion_original_message)

                    return

                model = converser_cog.conversation_threads[message.channel.id].model
                file_urls = []

                if files:
                    if (
                        "-vision" not in model
                        and image_understanding_model.get_is_usable()
                    ):
                        add_prompts = []
                        for num, file in enumerate(files):
                            thinking_embed = discord.Embed(
                                title=f"🤖💬 Interpreting attachment without GPT-Vision...",
                                color=0x808080,
                            )

                            thinking_embed.set_footer(
                                text="This may take a few seconds."
                            )
                            try:
                                thinking_message = await message.reply(
                                    embed=thinking_embed
                                )
                            except:
                                traceback.print_exc()
                                pass

                            try:
                                await message.channel.trigger_typing()
                            except Exception:
                                pass
                            async with aiofiles.tempfile.NamedTemporaryFile(
                                delete=False
                            ) as temp_file:
                                await file.save(temp_file.name)
                                try:
                                    (
                                        image_caption,
                                        llava_output,
                                        image_ocr,
                                    ) = await asyncio.gather(
                                        asyncio.to_thread(
                                            image_understanding_model.get_image_caption,
                                            temp_file.name,
                                        ),
                                        asyncio.to_thread(
                                            image_understanding_model.get_llava_answer,
                                            prompt,
                                            temp_file.name,
                                        ),
                                        image_understanding_model.do_image_ocr(
                                            temp_file.name
                                        ),
                                    )
                                    llava_output = "".join(list(llava_output))

                                    add_prompt = (
                                        f"BEGIN IMAGE {num} DATA\nImage Info-Caption: {image_caption}\nImage "
                                        f"Info-QA: {llava_output}\nImage Info-OCR: {image_ocr}\nEND IMAGE {num}\n DATA\n"
                                    )
                                    add_prompts.append(add_prompt)
                                    try:
                                        await thinking_message.delete()
                                    except:
                                        pass
                                except Exception:
                                    traceback.print_exc()
                                    await message.reply(
                                        "I wasn't able to understand the file you gave me."
                                    )
                                    await thinking_message.delete()
                                    return
                        prompt = (
                            "".join(add_prompts)
                            + f"Now, the original prompt "
                            + f"is given below, use the image understanding data to answer the question but don't "
                            f"refer directly to the data. Original Prompt: " + prompt
                        )
                    elif "-vision" in model:
                        file_urls = [file.url for file in files]
                        print("The file URLs were found to be" + str(file_urls))

                converser_cog.awaiting_responses.append(message.author.id)
                converser_cog.awaiting_thread_responses.append(message.channel.id)

                if not converser_cog.pinecone_service:
                    converser_cog.conversation_threads[
                        message.channel.id
                    ].history.append(
                        EmbeddedConversationItem(
                            f"\n{message.author.display_name}: {prompt} <|endofstatement|>\n",
                            0,
                            image_urls=file_urls,
                        )
                    )

                # increment the conversation counter for the user
                converser_cog.conversation_threads[message.channel.id].count += 1

            # Determine if we should draw an image and determine what to draw, and handle the drawing itself
            # TODO: This should be encapsulated better into some other service or function so we're not cluttering this text service file, this text service file is gross right now..
            if (
                "-vision" in model
                and not converser_cog.pinecone_service
                and converser_cog.conversation_threads[message.channel.id].drawable
            ):
                print("Checking for if the user asked to draw")
                draw_check_prompt = """
                Here are some good prompting tips:
                Describe the Image Content: Start your prompt with the type of image you want, such as "A photograph of...", "A 3D rendering of...", "A sketch of...", or "An illustration of...".
                Describe the Subject: Clearly state the subject of your image. It could be anything from a person or animal to an abstract concept. Be specific to guide the AI, e.g., "An illustration of an owl...", "A photograph of a president...", "A 3D rendering of a chair...".
                Add Relevant Details: Include details like colors, shapes, sizes, and textures. Rather than just saying "bear", specify the type (e.g., "brown and black, grizzly or polar"), surroundings (e.g., "a forest or mountain range"), and other details.
                Describe the Form and Style: Provide details about the form and style, using keywords like "abstract", "minimalist", or "surreal". You can also mention specific artists or artworks to mimic their style, e.g., "Like Salvador Dali" or "Like Andy Warhol’s Shot Marilyns painting".
                Define the Composition: Use keywords to define the composition, such as resolution, lighting style, aspect ratio, and camera view.
                Additional Tips:
                Use understandable keywords; avoid overly complicated or uncommon words.
                Keep prompts concise; aim for 3 to 7 words, but avoid being overly descriptive.
                Use multiple adjectives to describe your art’s subject, style, and composition.
                Avoid conflicting terms with opposite meanings.
                Use AI copywriting tools like ChatGPT for prompt generation.
                Research the specific AI art tool you’re using for recognized keywords.
                Examples:
                "A 3D rendering of a tree with bright yellow leaves and an abstract style."
                "An illustration of a mountain in the style of Impressionism with a wide aspect ratio."
                "A photograph of a steampunk alien taken from a low-angle viewpoint."
                "A sketch of a raccoon in bright colors and minimalist composition."       
                
                You will be given a set of conversation items and you will determine if the intent of the user(s) are to draw/create a picture or not, if the intent is to
                draw a picture, extract a prompt for the image to draw for use in systems like DALL-E. Respond with JSON after you determine intent to draw or not. In this format:
                
                {
                    "intent_to_draw": true/false,
                    "prompt": "prompt to draw",
                    "amount": 1
                }
                
                For example, you determined intent to draw a cat sitting on a chair:
                {
                    "intent_to_draw": true,
                    "prompt": "A cat sitting on a chair",
                    "amount": 1

                }
                For example, you determined no intent:
                {
                    "intent_to_draw": false,
                    "prompt": "",
                    "amount": 1
                }
                Make sure you use double quotes around all keys and values. Ensure to OMIT trailing commas.
                As you can see, the default amount should always be one, but a user can draw up to 4 images. Be hesitant to draw more than 3 images.
                Only signify an intent to draw when the user has explicitly asked you to draw, sometimes there may be situations where the user is asking you to brainstorm a prompt
                but not neccessarily draw it, if you are unsure, ask the user explicitly. Ensure your JSON strictly confirms, only output the raw json. no other text.
                """
                last_messages = converser_cog.conversation_threads[
                    message.channel.id
                ].history[
                    -6:
                ]  # Get the last 6 messages to determine context on whether we should draw
                last_messages = last_messages[1:]
                try:
                    thinking_message = await TextService.trigger_thinking(message)

                    response_json = await converser_cog.model.send_chatgpt_chat_request(
                        last_messages,
                        "gpt-4-vision-preview",
                        temp_override=0,
                        user_displayname=message.author.display_name,
                        bot_name=BOT_NAME,
                        system_prompt_override=draw_check_prompt,
                        respond_json=True,
                    )
                    await TextService.stop_thinking(thinking_message)
                    # This validation is only until we figure out what's wrong with the json response mode for vision.
                    if response_json["intent_to_draw"]:
                        thinking_message = await TextService.trigger_thinking(
                            message, is_drawing=True
                        )

                        links = await converser_cog.model.send_image_request_within_conversation(
                            response_json["prompt"],
                            quality="hd",
                            image_size="1024x1024",
                            style="vivid",
                            num_images=response_json["amount"],
                        )
                        await TextService.stop_thinking(thinking_message)

                        image_markdowns = []
                        for num, link in enumerate(links):
                            image_markdowns.append(f"[image{num}]({link})")
                        await message.reply(" ".join(image_markdowns))

                        converser_cog.conversation_threads[
                            message.channel.id
                        ].history.append(
                            EmbeddedConversationItem(
                                f"\nYou have just generated images for the user, notify the user about what you've drawn\n",
                                0,
                                image_urls=links,
                            )
                        )
                except:
                    try:
                        await message.reply(
                            "I encountered an error while trying to draw.."
                        )
                        await thinking_message.delete()
                        converser_cog.conversation_threads[
                            message.channel.id
                        ].history.append(
                            EmbeddedConversationItem(
                                f"\nYou just tried to generate an image but the generation failed. Notify the user of this now.>\n",
                                0,
                            )
                        )
                    except:
                        pass
                    traceback.print_exc()

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
            conversation_overrides = converser_cog.conversation_threads[
                message.channel.id
            ].get_overrides()
            overrides = Override(
                conversation_overrides["temperature"],
                conversation_overrides["top_p"],
                conversation_overrides["frequency_penalty"],
                conversation_overrides["presence_penalty"],
            )

            # Send an embed that tells the user that the bot is thinking
            thinking_message = await TextService.trigger_thinking(message)
            converser_cog.full_conversation_history[message.channel.id].append(prompt)

            if not converser_cog.pinecone_service:
                primary_prompt += BOT_NAME

            await TextService.encapsulated_send(
                converser_cog,
                message.channel.id,
                primary_prompt,
                message,
                overrides=overrides,
                model=converser_cog.conversation_threads[message.channel.id].model,
                custom_api_key=user_api_key,
                is_drawable=converser_cog.conversation_threads[
                    message.channel.id
                ].drawable,
            )

            # Delete the thinking embed
            await TextService.stop_thinking(thinking_message)

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
                    "You must set up your API key before typing in a GPT powered channel, type `/setup` to enter your API key."
                )
        return user_api_key

    @staticmethod
    async def process_conversation_edit(converser_cog, after, original_message):
        if after.author.id in converser_cog.redo_users:
            if after.id == original_message.get(after.author.id, None):
                response_message = converser_cog.redo_users[after.author.id].response
                ctx = converser_cog.redo_users[after.author.id].ctx
                await response_message.edit(content="Redoing prompt 🔄...")

                edited_content = await converser_cog.mention_to_username(
                    after, after.content
                )

                if after.channel.id in converser_cog.conversation_threads:
                    # Remove the last two elements from the history array and add the new <username>: prompt
                    converser_cog.conversation_threads[after.channel.id].history = (
                        converser_cog.conversation_threads[after.channel.id].history[
                            :-2
                        ]
                    )

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

                conversation_overrides = converser_cog.conversation_threads[
                    after.channel.id
                ].get_overrides()

                overrides = Override(
                    conversation_overrides["temperature"],
                    conversation_overrides["top_p"],
                    conversation_overrides["frequency_penalty"],
                    conversation_overrides["presence_penalty"],
                )

                await TextService.encapsulated_send(
                    converser_cog,
                    id=after.channel.id,
                    prompt=edited_content,
                    ctx=ctx,
                    response_message=response_message,
                    overrides=overrides,
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
        try:
            # Remove the button from the view/message
            self.clear_items()
            # Send a message to the user saying the view has timed out
            if self.message:
                # check if the timeout happens in a thread and if it's locked
                if isinstance(self.message.channel, discord.Thread):
                    if self.message.channel.locked:
                        return
                await self.message.edit(
                    view=None,
                )
            else:
                await self.ctx.edit(
                    view=None,
                )
        except Exception:
            pass  # Silently fail, as this usually means we were not able to retrieve the correct webhook token.


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
            and interaction.channel.id
            in self.converser_cog.conversation_thread_owners[user_id]
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

            await interaction.response.send_message(
                "Retrying your original request...", ephemeral=True, delete_after=15
            )

            await TextService.encapsulated_send(
                self.converser_cog,
                overrides=Override(None, None, None, None),
                id=user_id,
                prompt=prompt,
                instruction=instruction,
                ctx=ctx,
                model=self.model,
                response_message=response_message,
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
                    embed=EmbedStatics.get_invalid_api_response_embed(e),
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
