import asyncio
import random
import tempfile
import traceback
from io import BytesIO

import aiohttp
import discord
from PIL import Image

from models.user_model import RedoUser


class ImageService:
    def __init__(self):
        pass

    @staticmethod
    async def encapsulated_send(
        image_service_cog,
        user_id,
        prompt,
        ctx,
        response_message=None,
        vary=None,
        draw_from_optimizer=None,
        custom_api_key=None,
    ):
        """service function that takes input and returns an image generation

        Args:
            image_service_cog (Cog): The cog which contains draw related commands
            user_id (int): A discord user id
            prompt (string): Prompt for the model
            ctx (ApplicationContext): A discord ApplicationContext, from an interaction
            response_message (Message, optional): A discord message. Defaults to None.
            vary (bool, optional): If the image is a variation of another one. Defaults to None.
            draw_from_optimizer (bool, optional): If the prompt is passed from the optimizer command. Defaults to None.
            custom_api_key (str, optional): User defined OpenAI API key. Defaults to None.
        """
        await asyncio.sleep(0)
        # send the prompt to the model
        from_context = isinstance(ctx, discord.ApplicationContext)

        try:
            file, image_urls = await image_service_cog.model.send_image_request(
                ctx,
                prompt,
                vary=vary if not draw_from_optimizer else None,
                custom_api_key=custom_api_key,
            )

        # Error catching for API errors
        except aiohttp.ClientResponseError as e:
            message = (
                f"The API returned an invalid response: **{e.status}: {e.message}**"
            )
            if not from_context:
                await ctx.channel.send(message)
            else:
                await ctx.respond(message, ephemeral=True)
            return

        except ValueError as e:
            message = f"Error: {e}. Please try again with a different prompt."
            if not from_context:
                await ctx.channel.send(message)
            else:
                await ctx.respond(message, ephemeral=True)

            return

        # Start building an embed to send to the user with the results of the image generation
        embed = discord.Embed(
            title="Image Generation Results"
            if not vary
            else "Image Generation Results (Varying)"
            if not draw_from_optimizer
            else "Image Generation Results (Drawing from Optimizer)",
            description=f"{prompt}",
            color=0xC730C7,
        )

        # Add the image file to the embed
        embed.set_image(url=f"attachment://{file.filename}")

        if not response_message:  # Original generation case
            # Start an interaction with the user, we also want to send data embed=embed, file=file,
            # view=SaveView(image_urls, image_service_cog, image_service_cog.converser_cog)
            result_message = (
                await ctx.channel.send(
                    embed=embed,
                    file=file,
                )
                if not from_context
                else await ctx.respond(embed=embed, file=file)
            )

            await result_message.edit(
                view=SaveView(
                    ctx,
                    image_urls,
                    image_service_cog,
                    image_service_cog.converser_cog,
                    result_message,
                    custom_api_key=custom_api_key,
                )
            )

            image_service_cog.converser_cog.users_to_interactions[user_id] = []
            image_service_cog.converser_cog.users_to_interactions[user_id].append(
                result_message.id
            )

            # Get the actual result message object
            if from_context:
                result_message = await ctx.fetch_message(result_message.id)

            image_service_cog.redo_users[user_id] = RedoUser(
                prompt=prompt,
                message=ctx,
                ctx=ctx,
                response=result_message,
                instruction=None,
                codex=False,
                paginator=None,
            )

        else:
            if not vary:  # Editing case
                message = await response_message.edit(
                    embed=embed,
                    file=file,
                )
                await message.edit(
                    view=SaveView(
                        ctx,
                        image_urls,
                        image_service_cog,
                        image_service_cog.converser_cog,
                        message,
                        custom_api_key=custom_api_key,
                    )
                )
            else:  # Varying case
                if not draw_from_optimizer:
                    result_message = await response_message.edit_original_response(
                        content="Image variation completed!",
                        embed=embed,
                        file=file,
                    )
                    await result_message.edit(
                        view=SaveView(
                            ctx,
                            image_urls,
                            image_service_cog,
                            image_service_cog.converser_cog,
                            result_message,
                            True,
                            custom_api_key=custom_api_key,
                        )
                    )

                else:
                    result_message = await response_message.edit_original_response(
                        content="I've drawn the optimized prompt!",
                        embed=embed,
                        file=file,
                    )
                    await result_message.edit(
                        view=SaveView(
                            ctx,
                            image_urls,
                            image_service_cog,
                            image_service_cog.converser_cog,
                            result_message,
                            custom_api_key=custom_api_key,
                        )
                    )

                    image_service_cog.redo_users[user_id] = RedoUser(
                        prompt=prompt,
                        message=ctx,
                        ctx=ctx,
                        response=result_message,
                        instruction=None,
                        codex=False,
                        paginator=None,
                    )

                image_service_cog.converser_cog.users_to_interactions[user_id].append(
                    response_message.id
                )
                image_service_cog.converser_cog.users_to_interactions[user_id].append(
                    result_message.id
                )


class SaveView(discord.ui.View):
    def __init__(
        self,
        ctx,
        image_urls,
        cog,
        converser_cog,
        message,
        no_retry=False,
        only_save=None,
        custom_api_key=None,
    ):
        super().__init__(
            timeout=3600 if not only_save else None
        )  # 1 hour timeout for Retry, Save
        self.ctx = ctx
        self.image_urls = image_urls
        self.cog = cog
        self.no_retry = no_retry
        self.converser_cog = converser_cog
        self.message = message
        self.custom_api_key = custom_api_key
        for x in range(1, len(image_urls) + 1):
            self.add_item(SaveButton(x, image_urls[x - 1]))
        if not only_save:
            if not no_retry:
                self.add_item(
                    RedoButton(
                        self.cog,
                        converser_cog=self.converser_cog,
                        custom_api_key=self.custom_api_key,
                    )
                )
            for x in range(1, len(image_urls) + 1):
                self.add_item(
                    VaryButton(
                        x,
                        image_urls[x - 1],
                        self.cog,
                        converser_cog=self.converser_cog,
                        custom_api_key=self.custom_api_key,
                    )
                )

    # On the timeout event, override it and we want to clear the items.
    async def on_timeout(self):
        # Save all the SaveButton items, then clear all the items, then add back the SaveButton items, then
        # update the message
        self.clear_items()

        # Create a new view with the same params as this one, but pass only_save=True
        new_view = SaveView(
            self.ctx,
            self.image_urls,
            self.cog,
            self.converser_cog,
            self.message,
            self.no_retry,
            only_save=True,
        )

        # Set the view of the message to the new view
        await self.ctx.edit(view=new_view)


class VaryButton(discord.ui.Button):
    def __init__(self, number, image_url, cog, converser_cog, custom_api_key):
        super().__init__(style=discord.ButtonStyle.blurple, label="Vary " + str(number), custom_id="vary_button"+str(random.randint(10000000,99999999)))
        self.number = number
        self.image_url = image_url
        self.cog = cog
        self.converser_cog = converser_cog
        self.custom_api_key = custom_api_key

    async def callback(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        interaction_id = interaction.message.id

        if interaction_id not in self.converser_cog.users_to_interactions[user_id]:
            if len(self.converser_cog.users_to_interactions[user_id]) >= 2:
                interaction_id2 = interaction.id
                if (
                    interaction_id2
                    not in self.converser_cog.users_to_interactions[user_id]
                ):
                    await interaction.response.send_message(
                        content="You can not vary images in someone else's chain!",
                        ephemeral=True,
                    )
            else:
                await interaction.response.send_message(
                    content="You can only vary for images that you generated yourself!",
                    ephemeral=True,
                )
            return

        if user_id in self.cog.redo_users:
            response_message = await interaction.response.send_message(
                content="Varying image number " + str(self.number) + "..."
            )
            self.converser_cog.users_to_interactions[user_id].append(
                response_message.message.id
            )
            self.converser_cog.users_to_interactions[user_id].append(
                response_message.id
            )
            prompt = self.cog.redo_users[user_id].prompt

            asyncio.ensure_future(
                ImageService.encapsulated_send(
                    self.cog,
                    user_id,
                    prompt,
                    interaction.message,
                    response_message=response_message,
                    vary=self.image_url,
                    custom_api_key=self.custom_api_key,
                )
            )


class SaveButton(discord.ui.Button["SaveView"]):
    def __init__(self, number: int, image_url: str):
        super().__init__(style=discord.ButtonStyle.gray, label="Save " + str(number), custom_id="save_button"+str(random.randint(1000000,9999999)))
        self.number = number
        self.image_url = image_url

    async def callback(self, interaction: discord.Interaction):
        # If the image url doesn't start with "http", then we need to read the file from the URI, and then send the
        # file to the user as an attachment.
        try:
            if not self.image_url.startswith("http"):
                with open(self.image_url, "rb") as f:
                    image = Image.open(BytesIO(f.read()))
                    temp_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                    image.save(temp_file.name)

                    await interaction.response.send_message(
                        content="Here is your image for download (open original and save)",
                        file=discord.File(temp_file.name),
                        ephemeral=True,
                    )
            else:
                await interaction.response.send_message(
                    f"You can directly download this image from {self.image_url}",
                    ephemeral=True,
                )
        except Exception as e:
            await interaction.response.send_message(f"Error: {e}", ephemeral=True)
            traceback.print_exc()


class RedoButton(discord.ui.Button["SaveView"]):
    def __init__(self, cog, converser_cog, custom_api_key):
        super().__init__(style=discord.ButtonStyle.danger, label="Retry", custom_id="redo_button_draw_main")
        self.cog = cog
        self.converser_cog = converser_cog
        self.custom_api_key = custom_api_key

    async def callback(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        interaction_id = interaction.message.id

        if interaction_id not in self.converser_cog.users_to_interactions[user_id]:
            await interaction.response.send_message(
                content="You can only retry for prompts that you generated yourself!",
                ephemeral=True,
            )
            return

        # We have passed the intial check of if the interaction belongs to the user
        if user_id in self.cog.redo_users:
            # Get the message and the prompt and call encapsulated_send
            ctx = self.cog.redo_users[user_id].ctx
            prompt = self.cog.redo_users[user_id].prompt
            response_message = self.cog.redo_users[user_id].response
            message = await interaction.response.send_message(
                "Regenerating the image for your original prompt, check the original message.",
                ephemeral=True,
            )
            self.converser_cog.users_to_interactions[user_id].append(message.id)

            asyncio.ensure_future(
                ImageService.encapsulated_send(
                    self.cog,
                    user_id,
                    prompt,
                    ctx,
                    response_message,
                    custom_api_key=self.custom_api_key,
                )
            )
