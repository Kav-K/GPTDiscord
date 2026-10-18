import asyncio
import logging

import discord

from models.openai_model import OpenAIAPIError
from models.user_model import RedoUser
from utils.api_errors import openai_error_message

logger = logging.getLogger(__name__)


class ImageService:
    @staticmethod
    async def encapsulated_send(
        image_service_cog,
        user_id,
        prompt,
        ctx,
        response_message=None,
        vary=None,
        custom_api_key=None,
        quality=None,
        image_size=None,
        style=None,
        control_lock=None,
    ):
        """Generate an image and bind its controls to the delivered message."""
        file = None
        try:
            model = image_service_cog.model
            image_reference = vary
            if image_reference is not None:
                file, image_urls = await model.send_image_edit_request(
                    ctx,
                    prompt,
                    image_reference,
                    quality,
                    image_size,
                    style,
                    custom_api_key=custom_api_key,
                )
            else:
                file, image_urls = await model.send_image_request(
                    ctx,
                    prompt,
                    quality,
                    image_size,
                    style,
                    custom_api_key=custom_api_key,
                )
            embed = discord.Embed(
                title="Image Variation Results"
                if image_reference
                else "Image Generation Results",
                description=prompt[:4096],
                color=0xC730C7,
            )
            embed.set_image(url=f"attachment://{file.filename}")
            if response_message is None:
                message = (
                    await ctx.respond(embed=embed, file=file)
                    if isinstance(ctx, discord.ApplicationContext)
                    else await ctx.channel.send(embed=embed, file=file)
                )
                if isinstance(message, discord.Interaction):
                    message = await message.original_response()
                if isinstance(
                    message, (discord.WebhookMessage, discord.InteractionMessage)
                ):
                    # Image controls outlive Discord's 15-minute interaction
                    # token. Reuse its payload with bot-authenticated editing.
                    message = discord.Message(
                        state=image_service_cog.bot._connection,
                        channel=message.channel,
                        data=message._raw_data,
                    )
            else:
                # Pycord otherwise retains every old attachment on each Retry.
                message = await response_message.edit(
                    embed=embed, file=file, attachments=[]
                )
            request = RedoUser(
                prompt=prompt,
                message=ctx,
                ctx=ctx,
                response=message,
                instruction=None,
                paginator=None,
                quality=quality,
                image_size=image_size,
                style=style,
                image_reference=image_reference,
            )
            await message.edit(
                view=SaveView(
                    image_urls,
                    image_service_cog,
                    message,
                    request=request,
                    owner_id=user_id,
                    control_lock=control_lock,
                )
            )
            return message
        except OpenAIAPIError as error:
            message = openai_error_message(error)
        except Exception as error:
            logger.warning("Image generation failed (%s)", type(error).__name__)
            message = "The image could not be generated. Please try again later."
        finally:
            if isinstance(file, discord.File):
                file.close()
        if isinstance(ctx, discord.ApplicationContext):
            await ctx.respond(message, ephemeral=True)
        else:
            await ctx.channel.send(message)


class SaveView(discord.ui.View):
    def __init__(
        self,
        image_urls,
        cog,
        message,
        only_save=None,
        *,
        request=None,
        owner_id=None,
        control_lock=None,
    ):
        super().__init__(timeout=None if only_save else 3600)
        self.image_urls = image_urls
        self.cog = cog
        self.message = message
        self.request = request
        self.owner_id = owner_id
        self.lock = control_lock if control_lock is not None else asyncio.Lock()
        for number, reference in enumerate(image_urls, 1):
            self.add_item(SaveButton(number, reference))
            if not only_save and request is not None:
                self.add_item(VaryButton(number, reference))
        if not only_save and request is not None:
            self.add_item(RedoButton())

    async def run(self, interaction: discord.Interaction, *, image_reference=None):
        if (
            interaction.user.id != self.owner_id
            or interaction.message.id != self.message.id
            or interaction.channel.id != self.message.channel.id
        ):
            await interaction.response.send_message(
                "You can only change images that you generated yourself!",
                ephemeral=True,
            )
            return
        if self.lock.locked():
            await interaction.response.send_message(
                "Please wait for the current image request to finish.", ephemeral=True
            )
            return
        async with self.lock:
            from cogs.image_service_cog import (
                USER_INPUT_API_KEYS,
                USER_KEY_DB,
                PRE_MODERATE,
            )
            from services.moderations_service import Moderation
            from services.text_service import TextService

            ctx = discord.ApplicationContext(self.cog.bot, interaction)
            user_api_key = None
            if USER_INPUT_API_KEYS:
                user_api_key = await TextService.get_user_api_key(
                    interaction.user.id, ctx, USER_KEY_DB
                )
                if not user_api_key:
                    return
            await ctx.defer(invisible=image_reference is None)
            request = self.request
            try:
                if PRE_MODERATE and await Moderation.simple_moderate_and_respond(
                    request.prompt, ctx
                ):
                    return
                await ImageService.encapsulated_send(
                    self.cog,
                    self.owner_id,
                    request.prompt,
                    ctx,
                    response_message=(
                        interaction.message if image_reference is None else None
                    ),
                    vary=(
                        request.image_reference
                        if image_reference is None
                        else image_reference
                    ),
                    custom_api_key=user_api_key,
                    quality=request.quality,
                    image_size=request.image_size,
                    style=request.style,
                    control_lock=self.lock,
                )
            except Exception as error:
                logger.warning("Image control failed (%s)", type(error).__name__)
                await ctx.respond(
                    "The image could not be regenerated. Please try again later.",
                    ephemeral=True,
                )

    async def on_timeout(self):
        view = SaveView(
            self.image_urls,
            self.cog,
            self.message,
            only_save=True,
        )
        try:
            await self.message.edit(view=view)
        except discord.HTTPException:
            pass


class SaveButton(discord.ui.Button["SaveView"]):
    def __init__(self, number, image_url):
        super().__init__(style=discord.ButtonStyle.gray, label=f"Save {number}")
        self.image_url = image_url

    async def callback(self, interaction: discord.Interaction):
        try:
            if self.image_url.startswith(("https://", "http://")):
                await interaction.response.send_message(
                    f"You can directly download this image from {self.image_url}",
                    ephemeral=True,
                )
            else:
                await interaction.response.defer(ephemeral=True, invisible=False)
                # Generated sources are already PNG files; upload the source
                # directly instead of leaking a temporary copy per download.
                file = discord.File(self.image_url)
                try:
                    await interaction.followup.send(
                        "Here is your image for download (open original and save)",
                        file=file,
                        ephemeral=True,
                    )
                finally:
                    file.close()
        except Exception as error:
            logger.warning("Image download unavailable (%s)", type(error).__name__)
            await interaction.respond(
                "This image is no longer available to download.", ephemeral=True
            )


class RedoButton(discord.ui.Button["SaveView"]):
    def __init__(self):
        super().__init__(
            style=discord.ButtonStyle.danger,
            label="Retry",
            custom_id="redo_button_draw_main",
        )

    async def callback(self, interaction: discord.Interaction):
        await self.view.run(interaction)


class VaryButton(discord.ui.Button["SaveView"]):
    def __init__(self, number, image_reference):
        super().__init__(style=discord.ButtonStyle.blurple, label=f"Vary {number}")
        self.image_reference = image_reference

    async def callback(self, interaction: discord.Interaction):
        await self.view.run(interaction, image_reference=self.image_reference)
