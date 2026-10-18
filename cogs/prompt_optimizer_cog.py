import asyncio
import logging
import re

import discord

from models.openai_model import Models, OpenAIAPIError
from services.environment_service import EnvService
from services.image_service import ImageService
from services.moderations_service import Moderation
from services.text_service import TextService
from utils.api_errors import openai_error_message

USER_INPUT_API_KEYS = EnvService.get_user_input_api_keys()
USER_KEY_DB = EnvService.get_api_db()
PRE_MODERATE = EnvService.get_premoderate()
logger = logging.getLogger(__name__)


class ImgPromptOptimizer(discord.Cog, name="ImgPromptOptimizer"):
    """Optimize image prompts, with Draw and Retry bound to their own message."""

    _OPTIMIZER_PRETEXT = "Optimize the following text for image generation to have the most detailed and realistic image possible. Prompt:"

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
        super().__init__()
        self.bot = bot
        self.model = model
        self.converser_cog = converser_cog
        self.image_service_cog = image_service_cog
        self.OPTIMIZER_PRETEXT = self._OPTIMIZER_PRETEXT
        try:
            path = EnvService.find_shared_file("image_optimizer_pretext.txt")
            self.OPTIMIZER_PRETEXT = path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            logger.warning("Image optimizer instructions unavailable; using defaults")

    async def optimize_command(
        self, ctx: discord.ApplicationContext, prompt: str, response_message=None
    ):
        user_api_key = None
        if USER_INPUT_API_KEYS:
            user_api_key = await TextService.get_user_api_key(
                ctx.user.id, ctx, USER_KEY_DB
            )
            if not user_api_key:
                return
        # A component Retry edits the existing prompt; a slash command creates
        # a new response. Type 6 avoids leaving a separate loading message.
        await ctx.defer(invisible=response_message is not None)
        try:
            if PRE_MODERATE and await Moderation.simple_moderate_and_respond(
                prompt, ctx
            ):
                return
            final_prompt = (
                self.OPTIMIZER_PRETEXT
                + await self.converser_cog.mention_to_username(ctx, prompt)
            )
            if not final_prompt.endswith("."):
                final_prompt += "."
            response = await self.model.send_request(
                final_prompt,
                tokens=None,
                model=Models.LOW_USAGE_MODEL,
                max_tokens_override=2048,
                custom_api_key=user_api_key,
            )
            text = discord.utils.escape_mentions(
                response["choices"][0]["message"]["content"]
            )
            if len(text.split()) > 75:
                text = " ".join(text.split()[-70:])
            text = re.sub(r"^(?:Optimized Prompt:|Output Prompt:|Output:)\s*", "", text)
            view = OptimizeView(self, ctx.user.id, prompt)
            if response_message is None:
                message = await ctx.respond(text, view=view)
            else:
                view.message_id = response_message.id
                message = await response_message.edit(content=text, view=view)
            view.message_id = message.id
            return message
        except OpenAIAPIError as error:
            await ctx.respond(openai_error_message(error), ephemeral=True)
        except Exception as error:
            logger.warning("Prompt optimization failed (%s)", type(error).__name__)
            await ctx.respond(
                "The prompt could not be optimized. Please try again later.",
                ephemeral=True,
            )


class OptimizeView(discord.ui.View):
    def __init__(self, optimizer, owner_id, prompt):
        super().__init__(timeout=3600)
        self.optimizer = optimizer
        self.owner_id = owner_id
        self.prompt = prompt
        self.message_id = None
        self.lock = asyncio.Lock()
        self.add_item(RedoButton())
        self.add_item(DrawButton())

    async def run(self, interaction, *, draw=False):
        if (
            interaction.user.id != self.owner_id
            or interaction.message.id != self.message_id
        ):
            await interaction.response.send_message(
                "You can only use prompts that you generated yourself!", ephemeral=True
            )
            return
        if self.lock.locked():
            await interaction.response.send_message(
                "Please wait for the current request to finish.", ephemeral=True
            )
            return
        async with self.lock:
            ctx = discord.ApplicationContext(self.optimizer.bot, interaction)
            if not draw:
                await self.optimizer.optimize_command(
                    ctx, self.prompt, interaction.message
                )
                return
            user_api_key = None
            if USER_INPUT_API_KEYS:
                user_api_key = await TextService.get_user_api_key(
                    interaction.user.id, ctx, USER_KEY_DB
                )
                if not user_api_key:
                    return
            await ctx.defer(invisible=False)
            prompt = interaction.message.content
            try:
                if PRE_MODERATE and await Moderation.simple_moderate_and_respond(
                    prompt, ctx
                ):
                    return
                # Use the normal generation path: it saves complete image Retry
                # metadata and receives an actual followup message from the SDK.
                await ImageService.encapsulated_send(
                    self.optimizer.image_service_cog,
                    interaction.user.id,
                    prompt,
                    ctx,
                    custom_api_key=user_api_key,
                    image_size="1024x1024",
                    style="vivid",
                    quality="auto",
                )
            except Exception as error:
                logger.warning(
                    "Optimized image generation failed (%s)", type(error).__name__
                )
                await ctx.respond(
                    "The image could not be generated. Please try again later.",
                    ephemeral=True,
                )


class DrawButton(discord.ui.Button["OptimizeView"]):
    def __init__(self):
        super().__init__(
            style=discord.ButtonStyle.green,
            label="Draw",
            custom_id="draw_button_optimizer",
        )

    async def callback(self, interaction: discord.Interaction):
        await self.view.run(interaction, draw=True)


class RedoButton(discord.ui.Button["OptimizeView"]):
    def __init__(self):
        super().__init__(
            style=discord.ButtonStyle.danger,
            label="Retry",
            custom_id="redo_button_optimizer",
        )

    async def callback(self, interaction: discord.Interaction):
        await self.view.run(interaction)
