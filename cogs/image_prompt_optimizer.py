import datetime
import os
import re
import traceback
from collections import defaultdict

import discord
from discord.ext import commands

from models.deletion_service import Deletion
from models.user_model import RedoUser


class ImgPromptOptimizer(commands.Cog, name="ImgPromptOptimizer"):
    _OPTIMIZER_PRETEXT = "Optimize the following text for DALL-E image generation to have the most detailed and realistic image possible. Prompt:"

    def __init__(
        self,
        bot,
        usage_service,
        model,
        message_queue,
        deletion_queue,
        converser_cog,
        image_service_cog,
    ):
        self.bot = bot
        self.usage_service = usage_service
        self.model = model
        self.message_queue = message_queue
        self.OPTIMIZER_PRETEXT = self._OPTIMIZER_PRETEXT
        self.converser_cog = converser_cog
        self.image_service_cog = image_service_cog
        self.deletion_queue = deletion_queue

        try:
            # Try to read the image optimizer pretext from
            # the file system
            with open("image_optimizer_pretext.txt", "r") as file:
                self.OPTIMIZER_PRETEXT = file.read()
            print("Loaded image optimizer pretext from file system")
        except:
            traceback.print_exc()
            self.OPTIMIZER_PRETEXT = self._OPTIMIZER_PRETEXT

    # Command error handler
    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return
        elif isinstance(error, commands.InvalidEndOfQuotedStringError):
            await ctx.reply(
                "There was an error while parsing your input. Please ensure that all quotation marks are closed and there is a space after the last quotation mark"
            )
        raise error

    @commands.command()
    async def imgoptimize(self, ctx, *args):

        prompt = self.OPTIMIZER_PRETEXT
        # Add everything except the command to the prompt
        for arg in args:
            prompt += arg + " "

        print(
            f"Received an image optimization request for the following prompt: {prompt}"
        )
        # Get the token amount for the prompt
        tokens = self.usage_service.count_tokens(prompt)

        try:
            response = await self.model.send_request(
                prompt,
                ctx.message,
                tokens=tokens,
                top_p_override=1.0,
                temp_override=0.9,
                presence_penalty_override=0.5,
                best_of_override=1,
            )
            # THIS USES MORE TOKENS THAN A NORMAL REQUEST! This will use roughly 4000 tokens, and will repeat the query
            # twice because of the best_of_override=2 parameter. This is to ensure that the model does a lot of analysis, but is
            # also relatively cost-effective

            response_text = response["choices"][0]["text"]

            print(f"Received the following response: {response.__dict__}")

            if re.search(r"<@!?\d+>|<@&\d+>|<#\d+>", response_text):
                await ctx.reply("I'm sorry, I can't mention users, roles, or channels.")
                return

            response_message = await ctx.reply(response_text)
            self.converser_cog.users_to_interactions[ctx.message.author.id] = []
            self.converser_cog.users_to_interactions[ctx.message.author.id].append(
                response_message.id
            )

            self.converser_cog.redo_users[ctx.author.id] = RedoUser(
                prompt, ctx.message, response_message
            )
            self.converser_cog.redo_users[ctx.author.id].add_interaction(
                response_message.id
            )
            await response_message.edit(
                view=OptimizeView(
                    self.converser_cog, self.image_service_cog, self.deletion_queue
                )
            )

        # Catch the value errors raised by the Model object
        except ValueError as e:
            await ctx.reply(e)
            return

        # Catch all other errors, we want this to keep going if it errors out.
        except Exception as e:
            await ctx.reply("Something went wrong, please try again later")
            await ctx.channel.send(e)
            # print a stack trace
            traceback.print_exc()
            return


class OptimizeView(discord.ui.View):
    def __init__(self, converser_cog, image_service_cog, deletion_queue):
        super().__init__(timeout=None)
        self.cog = converser_cog
        self.image_service_cog = image_service_cog
        self.deletion_queue = deletion_queue
        self.add_item(RedoButton(self.cog, self.image_service_cog, self.deletion_queue))
        self.add_item(DrawButton(self.cog, self.image_service_cog, self.deletion_queue))


class DrawButton(discord.ui.Button["OptimizeView"]):
    def __init__(self, converser_cog, image_service_cog, deletion_queue):
        super().__init__(style=discord.ButtonStyle.green, label="Draw")
        self.converser_cog = converser_cog
        self.image_service_cog = image_service_cog
        self.deletion_queue = deletion_queue

    async def callback(self, interaction: discord.Interaction):

        user_id = interaction.user.id
        interaction_id = interaction.message.id

        if (
            interaction_id not in self.converser_cog.users_to_interactions[user_id]
            or interaction_id not in self.converser_cog.redo_users[user_id].interactions
        ):
            await interaction.response.send_message(
                content="You can only draw for prompts that you generated yourself!",
                ephemeral=True,
            )
            return

        msg = await interaction.response.send_message(
            "Drawing this prompt...", ephemeral=False
        )
        self.converser_cog.users_to_interactions[interaction.user.id].append(msg.id)
        self.converser_cog.users_to_interactions[interaction.user.id].append(
            interaction.id
        )
        self.converser_cog.users_to_interactions[interaction.user.id].append(
            interaction.message.id
        )

        # get the text content of the message that was interacted with
        prompt = interaction.message.content

        # Use regex to replace "Output Prompt:" loosely with nothing.
        # This is to ensure that the prompt is formatted correctly
        prompt = re.sub(r"Optimized Prompt: ?", "", prompt)

        # Call the image service cog to draw the image
        await self.image_service_cog.encapsulated_send(
            prompt, None, msg, True, True, user_id
        )


class RedoButton(discord.ui.Button["OptimizeView"]):
    def __init__(self, converser_cog, image_service_cog, deletion_queue):
        super().__init__(style=discord.ButtonStyle.danger, label="Retry")
        self.converser_cog = converser_cog
        self.image_service_cog = image_service_cog
        self.deletion_queue = deletion_queue

    async def callback(self, interaction: discord.Interaction):
        interaction_id = interaction.message.id

        # Get the user
        user_id = interaction.user.id

        if user_id in self.converser_cog.redo_users and self.converser_cog.redo_users[
            user_id
        ].in_interaction(interaction_id):
            # Get the message and the prompt and call encapsulated_send
            message = self.converser_cog.redo_users[user_id].message
            prompt = self.converser_cog.redo_users[user_id].prompt
            response_message = self.converser_cog.redo_users[user_id].response
            msg = await interaction.response.send_message(
                "Redoing your original request...", ephemeral=True, delete_after=20
            )
            await self.converser_cog.encapsulated_send(
                message, prompt, response_message
            )
        else:
            await interaction.response.send_message(
                content="You can only redo for prompts that you generated yourself!",
                ephemeral=True,
                delete_after=10,
            )
