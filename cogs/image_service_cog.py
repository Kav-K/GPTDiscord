import asyncio
import os
import traceback

import discord

# We don't use the converser cog here because we want to be able to redo for the last images and text prompts at the same time
from sqlitedict import SqliteDict

from services.environment_service import EnvService
from services.image_service import ImageService
from services.moderations_service import Moderation
from services.text_service import TextService
from utils.safe_ctx_respond import safe_ctx_respond

users_to_interactions = {}
ALLOWED_GUILDS = EnvService.get_allowed_guilds()

USER_INPUT_API_KEYS = EnvService.get_user_input_api_keys()
USER_KEY_DB = EnvService.get_api_db()
PRE_MODERATE = EnvService.get_premoderate()


class DrawDallEService(discord.Cog, name="DrawDallEService"):
    """Cog containing a draw commands and file management for saved images"""

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

    async def draw_command(
        self,
        ctx: discord.ApplicationContext,
        prompt: str,
        quality: str,
        image_size: str,
        style: str,
        from_action=False,
    ):
        """With an ApplicationContext and prompt, send a dalle image to the invoked channel. Ephemeral if from an action"""
        user_api_key = None
        if USER_INPUT_API_KEYS:
            user_api_key = await TextService.get_user_api_key(
                ctx.user.id, ctx, USER_KEY_DB
            )
            if not user_api_key:
                return

        await ctx.defer()

        # Check the opener for bad content.
        if PRE_MODERATE:
            if await Moderation.simple_moderate_and_respond(prompt, ctx):
                return

        user = ctx.user

        if user == self.bot.user:
            return

        try:
            asyncio.ensure_future(
                ImageService.encapsulated_send(
                    self,
                    user.id,
                    prompt,
                    ctx,
                    custom_api_key=user_api_key,
                    dalle_3=True,
                    quality=quality,
                    image_size=image_size,
                    style=style,
                )
            )

        except Exception as e:
            print(e)
            traceback.print_exc()
            await safe_ctx_respond(ctx=ctx, content="Something went wrong. Please try again later.", ephemeral=from_action)
            await ctx.send_followup(e, ephemeral=from_action)

    async def draw_old_command(
        self, ctx: discord.ApplicationContext, prompt: str, from_action=False
    ):
        """With an ApplicationContext and prompt, send a dalle image to the invoked channel. Ephemeral if from an action"""
        user_api_key = None
        if USER_INPUT_API_KEYS:
            user_api_key = await TextService.get_user_api_key(
                ctx.user.id, ctx, USER_KEY_DB
            )
            if not user_api_key:
                return

        await ctx.defer()

        # Check the opener for bad content.
        if PRE_MODERATE:
            if await Moderation.simple_moderate_and_respond(prompt, ctx):
                return

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
            await safe_ctx_respond(ctx=ctx, content="Something went wrong. Please try again later.", ephemeral=from_action)

            await ctx.send_followup(e, ephemeral=from_action)

    async def draw_action(self, ctx, message):
        """decoupler to handle context actions for the draw command"""
        await self.draw_command(
            ctx,
            message.content,
            quality="hd",
            image_size="1024x1024",
            style="natural",
            from_action=True,
        )

    async def local_size_command(self, ctx: discord.ApplicationContext):
        """Get the folder size of the image folder"""
        await ctx.defer()

        image_path = self.model.IMAGE_SAVE_PATH
        total_size = 0
        for dirpath, dirnames, filenames in os.walk(image_path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                total_size += os.path.getsize(fp)

        # Format the size to be in MB and send.
        total_size = total_size / 1000000
        await safe_ctx_respond(ctx=ctx, content=f"The size of the local images folder is {total_size} MB.")

    async def clear_local_command(self, ctx):
        """Delete all local images"""
        await ctx.defer()

        image_path = self.model.IMAGE_SAVE_PATH
        for dirpath, dirnames, filenames in os.walk(image_path):
            for f in filenames:
                try:
                    fp = os.path.join(dirpath, f)
                    os.remove(fp)
                except Exception as e:
                    print(e)

        await ctx.respond("Local images cleared.")
