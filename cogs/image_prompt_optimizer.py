import os
import re
import traceback

from discord.ext import commands


class ImgPromptOptimizer(commands.Cog, name='ImgPromptOptimizer'):

    _OPTIMIZER_PRETEXT = "Optimize the following text for DALL-E image generation to have the most detailed and realistic image possible. Prompt:"

    def __init__(self, bot, usage_service, model, message_queue, deletion_queue):
        self.bot = bot
        self.usage_service = usage_service
        self.model = model
        self.message_queue = message_queue
        self.OPTIMIZER_PRETEXT = self._OPTIMIZER_PRETEXT

        try:
            # Try to read the image optimizer pretext from
            # the file system
            with open('image_optimizer_pretext.txt', 'r') as file:
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

        print(f"Received an image optimization request for the following prompt: {prompt}")

        try:
            response = self.model.send_request(prompt, ctx.message)
            response_text = response["choices"][0]["text"]

            print(f"Received the following response: {response.__dict__}")

            if re.search(r"<@!?\d+>|<@&\d+>|<#\d+>", response_text):
                await ctx.reply("I'm sorry, I can't mention users, roles, or channels.")
                return

            response_message = await ctx.reply(response_text)


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
