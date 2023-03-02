import re
import traceback

import discord
from sqlitedict import SqliteDict

from models.openai_model import Override, Models
from services.environment_service import EnvService
from models.user_model import RedoUser
from services.image_service import ImageService
from services.moderations_service import Moderation

from services.text_service import TextService

ALLOWED_GUILDS = EnvService.get_allowed_guilds()
USER_INPUT_API_KEYS = EnvService.get_user_input_api_keys()
USER_KEY_DB = EnvService.get_api_db()
PRE_MODERATE = EnvService.get_premoderate()


class ImgPromptOptimizer(discord.Cog, name="ImgPromptOptimizer"):
    """cog containing the optimizer command"""

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
        super().__init__()
        self.bot = bot
        self.usage_service = usage_service
        self.model = model
        self.message_queue = message_queue
        self.OPTIMIZER_PRETEXT = self._OPTIMIZER_PRETEXT
        self.converser_cog = converser_cog
        self.image_service_cog = image_service_cog
        self.deletion_queue = deletion_queue

        try:
            image_pretext_path = EnvService.find_shared_file(
                "image_optimizer_pretext.txt"
            )
            # Try to read the image optimizer pretext from
            # the file system
            with image_pretext_path.open("r") as file:
                self.OPTIMIZER_PRETEXT = file.read()
            print(f"Loaded image optimizer pretext from {image_pretext_path}")
        except Exception:
            traceback.print_exc()
            self.OPTIMIZER_PRETEXT = self._OPTIMIZER_PRETEXT

    async def optimize_command(self, ctx: discord.ApplicationContext, prompt: str):
        """Command handler. Given a string it generates an output that's fitting for image generation"""
        user_api_key = None
        if USER_INPUT_API_KEYS:
            user_api_key = await TextService.get_user_api_key(
                ctx.user.id, ctx, USER_KEY_DB
            )
            if not user_api_key:
                return

        await ctx.defer()

        user = ctx.user

        final_prompt = self.OPTIMIZER_PRETEXT
        # replace mentions with nicknames for the prompt
        final_prompt += await self.converser_cog.mention_to_username(ctx, prompt)

        # If the prompt doesn't end in a period, terminate it.
        if not final_prompt.endswith("."):
            final_prompt += "."

        # Check the opener for bad content.
        if PRE_MODERATE:
            if await Moderation.simple_moderate_and_respond(prompt, ctx):
                return

        # Get the token amount for the prompt
        # tokens = self.usage_service.count_tokens(final_prompt)

        try:
            response = await self.model.send_request(
                final_prompt,
                tokens=60,
                top_p_override=1.0,
                temp_override=0.9,
                presence_penalty_override=0.5,
                best_of_override=1,
                max_tokens_override=60,
                custom_api_key=user_api_key,
            )

            # THIS USES MORE TOKENS THAN A NORMAL REQUEST! This will use roughly 4000 tokens, and will repeat the query
            # twice because of the best_of_override=2 parameter. This is to ensure that the model does a lot of analysis, but is
            # also relatively cost-effective

            response_text = str(response["choices"][0]["text"]) if not self.model.model in Models.CHATGPT_MODELS else response["choices"][0]["message"]["content"]

            # escape any mentions
            response_text = discord.utils.escape_mentions(response_text)

            # If the response_message is > 75 words, concatenate to the last 70th word
            # TODO Temporary workaround until prompt is adjusted to make the optimized prompts shorter.
            try:
                if len(response_text.split()) > 75:
                    response_text = " ".join(response_text.split()[-70:])
            except Exception:
                pass

            response_message = await ctx.respond(
                response_text.replace("Optimized Prompt:", "")
                .replace("Output Prompt:", "")
                .replace("Output:", "")
            )

            self.converser_cog.users_to_interactions[user.id] = []
            self.converser_cog.users_to_interactions[user.id].append(
                response_message.id
            )

            self.converser_cog.redo_users[user.id] = RedoUser(
                prompt=final_prompt,
                message=ctx,
                ctx=ctx,
                response=response_message,
                instruction=None,
                codex=False,
                paginator=None,
            )
            self.converser_cog.redo_users[user.id].add_interaction(response_message.id)
            await response_message.edit(
                view=OptimizeView(
                    self.converser_cog,
                    self.image_service_cog,
                    self.deletion_queue,
                    custom_api_key=user_api_key,
                )
            )

        # Catch the value errors raised by the Model object
        except ValueError as e:
            await ctx.respond(e)
            return

        # Catch all other errors, we want this to keep going if it errors out.
        except Exception as e:
            await ctx.respond("Something went wrong, please try again later")
            await ctx.send_followup(e)
            # print a stack trace
            traceback.print_exc()
            return


class OptimizeView(discord.ui.View):
    def __init__(
        self, converser_cog, image_service_cog, deletion_queue, custom_api_key=None
    ):
        super().__init__(timeout=None)
        self.cog = converser_cog
        self.image_service_cog = image_service_cog
        self.deletion_queue = deletion_queue
        self.custom_api_key = custom_api_key
        self.add_item(
            RedoButton(
                self.cog,
                self.image_service_cog,
                self.deletion_queue,
                custom_api_key=self.custom_api_key,
            )
        )
        self.add_item(
            DrawButton(
                self.cog,
                self.image_service_cog,
                self.deletion_queue,
                custom_api_key=self.custom_api_key,
            )
        )


class DrawButton(discord.ui.Button["OptimizeView"]):
    def __init__(
        self, converser_cog, image_service_cog, deletion_queue, custom_api_key
    ):
        super().__init__(
            style=discord.ButtonStyle.green,
            label="Draw",
            custom_id="draw_button_optimizer",
        )
        self.converser_cog = converser_cog
        self.image_service_cog = image_service_cog
        self.deletion_queue = deletion_queue
        self.custom_api_key = custom_api_key

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
        await ImageService.encapsulated_send(
            self.image_service_cog,
            user_id,
            prompt,
            interaction,
            msg,
            True,
            True,
            custom_api_key=self.custom_api_key,
        )


class RedoButton(discord.ui.Button["OptimizeView"]):
    def __init__(
        self, converser_cog, image_service_cog, deletion_queue, custom_api_key=None
    ):
        super().__init__(
            style=discord.ButtonStyle.danger,
            label="Retry",
            custom_id="redo_button_optimizer",
        )
        self.converser_cog = converser_cog
        self.image_service_cog = image_service_cog
        self.deletion_queue = deletion_queue
        self.custom_api_key = custom_api_key

    async def callback(self, interaction: discord.Interaction):
        interaction_id = interaction.message.id

        # Get the user
        user_id = interaction.user.id

        if user_id in self.converser_cog.redo_users and self.converser_cog.redo_users[
            user_id
        ].in_interaction(interaction_id):
            # Get the message and the prompt and call encapsulated_send
            ctx = self.converser_cog.redo_users[user_id].ctx
            # message = self.converser_cog.redo_users[user_id].message
            prompt = self.converser_cog.redo_users[user_id].prompt
            response_message = self.converser_cog.redo_users[user_id].response
            await interaction.response.send_message(
                "Redoing your original request...", ephemeral=True, delete_after=20
            )
            overrides = Override(1.0, 0.9, 0.5)
            await TextService.encapsulated_send(
                self.converser_cog,
                id=user_id,
                prompt=prompt,
                overrides=overrides,
                ctx=ctx,
                response_message=response_message,
                custom_api_key=self.custom_api_key,
            )
        else:
            await interaction.response.send_message(
                content="You can only redo for prompts that you generated yourself!",
                ephemeral=True,
                delete_after=10,
            )
