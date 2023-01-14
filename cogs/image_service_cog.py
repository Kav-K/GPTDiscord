import asyncio
import os
import tempfile
import traceback
from io import BytesIO

import aiohttp
import discord
from PIL import Image

# We don't use the converser cog here because we want to be able to redo for the last images and text prompts at the same time
from sqlitedict import SqliteDict

from cogs.text_service_cog import GPT3ComCon
from services.environment_service import EnvService
from models.user_model import RedoUser
from services.image_service import ImageService
from services.text_service import TextService

users_to_interactions = {}
ALLOWED_GUILDS = EnvService.get_allowed_guilds()

USER_INPUT_API_KEYS = EnvService.get_user_input_api_keys()
USER_KEY_DB = None
if USER_INPUT_API_KEYS:
    USER_KEY_DB = SqliteDict("user_key_db.sqlite")


class DrawDallEService(discord.Cog, name="DrawDallEService"):
    def __init__(
        self, bot, usage_service, model, message_queue, deletion_queue, converser_cog
    ):
        super().__init__()
        self.bot = bot
        self.usage_service = usage_service
        self.model = model
        self.message_queue = message_queue
        self.deletion_queue = deletion_queue
        self.converser_cog = converser_cog
        print("Draw service initialized")
        self.redo_users = {}

    async def draw_command(self, ctx: discord.ApplicationContext, prompt: str):
        user_api_key = None
        if USER_INPUT_API_KEYS:
            user_api_key = await TextService.get_user_api_key(
                ctx.user.id, ctx, USER_KEY_DB
            )
            if not user_api_key:
                return

        await ctx.defer()

        user = ctx.user

        if user == self.bot.user:
            return

        try:
            asyncio.ensure_future(
                ImageService.encapsulated_send(
                    self, user.id, prompt, ctx, custom_api_key=user_api_key
                )
            )

        except Exception as e:
            print(e)
            traceback.print_exc()
            await ctx.respond("Something went wrong. Please try again later.")
            await ctx.send_followup(e)

    async def local_size_command(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        # Get the size of the dall-e images folder that we have on the current system.

        image_path = self.model.IMAGE_SAVE_PATH
        total_size = 0
        for dirpath, dirnames, filenames in os.walk(image_path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                total_size += os.path.getsize(fp)

        # Format the size to be in MB and send.
        total_size = total_size / 1000000
        await ctx.respond(f"The size of the local images folder is {total_size} MB.")

    async def clear_local_command(self, ctx):
        await ctx.defer()

        # Delete all the local images in the images folder.
        image_path = self.model.IMAGE_SAVE_PATH
        for dirpath, dirnames, filenames in os.walk(image_path):
            for f in filenames:
                try:
                    fp = os.path.join(dirpath, f)
                    os.remove(fp)
                except Exception as e:
                    print(e)

        await ctx.respond("Local images cleared.")
