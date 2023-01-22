import traceback

import aiohttp
import discord

from models.deepl_model import TranslationModel
from services.environment_service import EnvService


ALLOWED_GUILDS = EnvService.get_allowed_guilds()


def build_translation_embed(
    text,
    translated_text,
    translated_language,
    detected_language,
    requestor: discord.User,
):
    """Build an embed for the translation"""
    embed_description = (
        f"**Original Text:** \n\n{text}\n\n **Translated Text:** \n\n{translated_text}"
    )
    embed = discord.Embed(
        title=f"Translation from {detected_language} to {translated_language}",
        description=embed_description,
        color=0x311432,
    )
    embed.set_footer(
        text=f"Requested by {requestor.name}#{requestor.discriminator}",
        icon_url=requestor.avatar.url,
    )

    return embed


class TranslationService(discord.Cog, name="TranslationService"):
    """Cog containing translation commands and retrieval of translation services"""

    def __init__(
        self,
        bot,
        translation_model,
    ):
        super().__init__()
        self.bot = bot
        self.translation_model = translation_model
        # Make a mapping of all the country codes and their full country names:

    def build_supported_language_embed(self):
        """Build an embed for the translation"""
        embed = discord.Embed(
            title="Translator supported languages",
            color=0x311432,
        )
        # Add the list of supported languages in a nice format
        embed.add_field(
            name="Languages",
            value=", ".join(
                [f"{name}" for name in TranslationModel.get_all_country_names()]
            ),
            inline=False,
        )

        return embed

    async def translate_command(self, ctx, text, target_language, formality):
        """Command handler for the translation command"""
        await ctx.defer()
        # TODO Add pagination!

        if (
            target_language.lower().strip()
            not in TranslationModel.get_all_country_names(lower=True)
        ):
            await ctx.respond(
                f"The language {target_language} is not recognized or supported. Please use `/languages` to see the list of supported languages."
            )
            return

        try:
            (
                response,
                detected_language,
            ) = await self.translation_model.send_translate_request(
                text,
                TranslationModel.get_country_code_from_name(target_language),
                formality,
            )
        except aiohttp.ClientResponseError as e:
            await ctx.respond(f"There was an error with the DeepL API: {e.message}")
            return

        await ctx.respond(
            embed=build_translation_embed(
                text,
                response,
                target_language,
                TranslationModel.get_country_name_from_code(detected_language),
                ctx.user,
            )
        )

    async def translate_action(self, ctx: discord.ApplicationContext, message):
        await ctx.defer(ephemeral=True)
        # If the message is only an embed and there's no content, don't translate.
        if message.content == "" and len(message.embeds) > 0:
            await ctx.respond(
                "Cannot translate an embed.", ephemeral=True, delete_after=30
            )
            return

        if len(message.content) > 2000:
            await ctx.respond(
                "Message is too long to translate.", ephemeral=True, delete_after=30
            )
            return

        selection_message = await ctx.respond(
            "Select language", ephemeral=True, delete_after=60
        )
        await selection_message.edit(
            view=TranslateView(
                self.translation_model, message, selection_message, ctx.user
            )
        )

    async def languages_command(self, ctx):
        """Show all languages supported for translation"""
        await ctx.defer()
        await ctx.respond(embed=self.build_supported_language_embed())


class TranslateView(discord.ui.View):
    def __init__(self, translation_model, message, selection_message, requestor):
        super().__init__()
        self.language_long = None
        self.language = None
        self.translation_model = translation_model
        self.message = message
        self.selection_message = selection_message
        self.formality = None
        self.requestor = requestor

    @discord.ui.select(  # the decorator that lets you specify the properties of the select menu
        placeholder="Language",  # the placeholder text that will be displayed if nothing is selected
        min_values=1,  # the minimum number of values that must be selected by the users
        max_values=1,  # the maximum number of values that can be selected by the users
        options=[  # the list of options from which users can choose, a required field
            discord.SelectOption(
                label=name,
            )
            for name in TranslationModel.get_all_country_names()
        ],
    )
    async def select_callback(
        self, select, interaction
    ):  # the function called when the user is done selecting options
        try:
            self.language = TranslationModel.get_country_code_from_name(
                select.values[0]
            )
            self.language_long = select.values[0]
            await interaction.response.defer()
        except:
            traceback.print_exc()

    @discord.ui.select(
        placeholder="Formality (optional)",
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(label="Prefer more", value="prefer_more"),
            discord.SelectOption(label="default", value="default"),
            discord.SelectOption(label="Prefer less", value="prefer_less"),
        ],
    )
    async def formality_callback(self, select, interaction):
        try:
            self.formality = select.values[0]
            await interaction.response.defer()
        except aiohttp.ClientResponseError as e:
            await interaction.response.send_message(
                f"There was an error with the DeepL API: {e.message}",
                ephemeral=True,
                delete_after=15,
            )
            return
        except Exception as e:
            await interaction.response.send_message(
                f"There was an error: {e}", ephemeral=True, delete_after=15
            )
            return

    # A button "Translate"
    @discord.ui.button(label="Translate", style=discord.ButtonStyle.green)
    async def button_callback(self, button, interaction):
        if not self.language or not self.language_long:
            await interaction.response.send_message(
                "Please select a language first.", ephemeral=True, delete_after=15
            )
            return
        try:
            (
                response,
                detected_language,
            ) = await self.translation_model.send_translate_request(
                self.message.content,
                self.language,
                self.formality,
            )
            await self.message.reply(
                mention_author=False,
                embed=build_translation_embed(
                    self.message.content,
                    response,
                    self.language_long,
                    TranslationModel.get_country_name_from_code(detected_language),
                    self.requestor,
                ),
            )
            await self.selection_message.delete()
        except aiohttp.ClientResponseError as e:
            await interaction.response.send_message(
                f"There was an error with the DeepL API: {e.message}",
                ephemeral=True,
                delete_after=15,
            )
            return
        except discord.errors.HTTPException as e:
            if e.code == 50035:
                await interaction.response.send_message(
                    "Message was too long to translate.",
                    ephemeral=True,
                    delete_after=15,
                )
                return
        except Exception as e:
            await interaction.response.send_message(
                f"There was an error: {e}", ephemeral=True, delete_after=15
            )
            traceback.print_exc()
            return
