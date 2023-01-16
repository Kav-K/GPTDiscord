import asyncio
import os
import traceback

import aiohttp
import discord

# We don't use the converser cog here because we want to be able to redo for the last images and text prompts at the same time
from sqlitedict import SqliteDict

from models.deepl_model import TranslationModel
from services.environment_service import EnvService
from services.image_service import ImageService
from services.text_service import TextService

ALLOWED_GUILDS = EnvService.get_allowed_guilds()


def build_translation_embed(text, translated_text, translated_language):
    """Build an embed for the translation"""
    embed = discord.Embed(
        title=f"Translation to " + translated_language,
        color=0x311432,
    )
    embed.add_field(name="Original text", value=text, inline=False)
    embed.add_field(name="Translated Text", value=translated_text, inline=False)

    return embed


class TranslationService(discord.Cog, name="TranslationService"):
    """Cog containing a draw commands and file management for saved images"""

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
            title=f"Translator supported languages",
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
        """Delete all local images"""
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
            response = await self.translation_model.send_translate_request(
                text, TranslationModel.get_country_code_from_name(target_language), formality
            )
        except aiohttp.ClientResponseError as e:
            await ctx.respond(f"There was an error with the DeepL API: {e.message}")
            return

        await ctx.respond(
            embed=build_translation_embed(text, response, target_language)
        )

    async def translate_action(self, ctx, message):
        await ctx.defer(ephemeral=True)
        selection_message = await ctx.respond(
            "Select language", ephemeral=True, delete_after=60
        )
        await selection_message.edit(
            view=TranslateView(self.translation_model, message, selection_message)
        )

    async def languages_command(self, ctx):
        """Show all languages supported for translation"""
        await ctx.defer()
        await ctx.respond(embed=self.build_supported_language_embed())


class TranslateView(discord.ui.View):
    def __init__(self, translation_model, message, selection_message):
        super().__init__()
        self.translation_model = translation_model
        self.message = message
        self.selection_message = selection_message

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
            response = await self.translation_model.send_translate_request(
                self.message.content,
                TranslationModel.get_country_code_from_name(select.values[0]),
            )
            await self.message.reply(
                mention_author=False,
                embed=build_translation_embed(
                    self.message.content, response, select.values[0]
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
        except Exception as e:
            await interaction.response.send_message(
                f"There was an error: {e}", ephemeral=True, delete_after=15
            )
            return
