import os
import logging

import discord

from services.environment_service import EnvService
from services.image_service import ImageService
from services.moderations_service import Moderation
from services.text_service import TextService
from utils.safe_ctx_respond import safe_ctx_respond

USER_INPUT_API_KEYS = EnvService.get_user_input_api_keys()
USER_KEY_DB = EnvService.get_api_db()
PRE_MODERATE = EnvService.get_premoderate()
logger = logging.getLogger(__name__)


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

        await ctx.defer(invisible=False)
        try:
            if PRE_MODERATE and await Moderation.simple_moderate_and_respond(
                prompt, ctx
            ):
                return
            if ctx.user == self.bot.user:
                return
            message = await ImageService.encapsulated_send(
                self,
                ctx.user.id,
                prompt,
                ctx,
                custom_api_key=user_api_key,
                dalle_3=True,
                quality=quality,
                image_size=image_size,
                style=style,
            )
            if message is not None:
                guild = getattr(ctx.guild, "name", None) or str(
                    ctx.interaction.guild_id or "DM"
                )
                self.usage_service.update_usage_memory(guild, "image_drawn", 1)

        except Exception as error:
            logger.warning("Image command failed (%s)", type(error).__name__)
            await safe_ctx_respond(
                ctx=ctx,
                content="Something went wrong. Please try again later.",
                ephemeral=from_action,
            )

    async def draw_old_command(
        self, ctx: discord.ApplicationContext, prompt: str, from_action=False
    ):
        """Keep the original slash command as an alias for current image generation."""
        await self.draw_command(
            ctx,
            prompt,
            quality="auto",
            image_size="1024x1024",
            style="vivid",
            from_action=from_action,
        )

    async def draw_action(self, ctx, message):
        """decoupler to handle context actions for the draw command"""
        await self.draw_command(
            ctx,
            message.content,
            quality="auto",
            image_size="1024x1024",
            style="vivid",
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
        await safe_ctx_respond(
            ctx=ctx, content=f"The size of the local images folder is {total_size} MB."
        )

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
