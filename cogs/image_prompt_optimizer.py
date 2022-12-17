import datetime
import os
import re
import traceback

import discord
from discord.ext import commands

from models.deletion_service import Deletion

redo_users = {}


class RedoUser:
    def __init__(self, prompt, message, response):
        self.prompt = prompt
        self.message = message
        self.response = response


class ImgPromptOptimizer(commands.Cog, name="ImgPromptOptimizer"):
    _OPTIMIZER_PRETEXT = "Optimize the following text for DALL-E image generation to have the most detailed and realistic image possible. Prompt:"

    def __init__(
        self, bot, usage_service, model, message_queue, deletion_queue, converser_cog
    ):
        self.bot = bot
        self.usage_service = usage_service
        self.model = model
        self.message_queue = message_queue
        self.OPTIMIZER_PRETEXT = self._OPTIMIZER_PRETEXT
        self.converser_cog = converser_cog

        try:
            # Try to read the image optimizer pretext from
            # the file system
            with open("image_optimizer_pretext.txt", "r") as file:
                self.OPTIMIZER_PRETEXT = file.read()
            print("Loaded image optimizer pretext from file system")
        except:
            traceback.print_exc()
            self.OPTIMIZER_PRETEXT = self._OPTIMIZER_PRETEXT

    @commands.command()
    async def imgoptimize(self, ctx, *args):

        prompt = self.OPTIMIZER_PRETEXT
        # Add everything except the command to the prompt
        for arg in args:
            prompt += arg + " "

        print(
            f"Received an image optimization request for the following prompt: {prompt}"
        )

        try:
            response = self.model.send_request(
                prompt,
                ctx.message,
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

            redo_users[ctx.author.id] = RedoUser(prompt, ctx.message, response_message)
            RedoButtonView.bot = self.converser_cog
            await response_message.edit(view=RedoButtonView())

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


class RedoButtonView(
    discord.ui.View
):  # Create a class called MyView that subclasses discord.ui.View
    @discord.ui.button(
        label="", style=discord.ButtonStyle.primary, emoji="🔄"
    )  # Create a button with the label "😎 Click me!" with color Blurple
    async def button_callback(self, button, interaction):
        msg = await interaction.response.send_message(
            "Redoing your original request...", ephemeral=True
        )

        # Put the message into the deletion queue with a timestamp of 10 seconds from now to be deleted
        deletion = Deletion(
            msg, (datetime.datetime.now() + datetime.timedelta(seconds=10)).timestamp()
        )
        await self.bot.deletion_queue.put(deletion)

        # Get the user
        user_id = interaction.user.id

        if user_id in redo_users:
            # Get the message and the prompt and call encapsulated_send
            message = redo_users[user_id].message
            prompt = redo_users[user_id].prompt
            response_message = redo_users[user_id].response
            await self.bot.encapsulated_send(message, prompt, response_message)
