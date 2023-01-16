from pathlib import Path
import os
import re

import discord

from models.deepl_model import TranslationModel
from services.usage_service import UsageService
from models.openai_model import Model
from services.environment_service import EnvService

usage_service = UsageService(Path(os.environ.get("DATA_DIR", os.getcwd())))
model = Model(usage_service)


class Settings_autocompleter:
    """autocompleter for the settings command"""

    async def get_settings(ctx: discord.AutocompleteContext):
        """get settings for the settings option"""
        SETTINGS = [
            re.sub("^_", "", key)
            for key in model.__dict__.keys()
            if key not in model._hidden_attributes
        ]
        return [
            parameter
            for parameter in SETTINGS
            if parameter.startswith(ctx.value.lower())
        ][:25]

    async def get_value(
        ctx: discord.AutocompleteContext,
    ):  # Behaves a bit weird if you go back and edit the parameter without typing in a new command
        """gets valid values for the value option"""
        values = {
            "max_conversation_length": [str(num) for num in range(1, 500, 2)],
            "num_images": [str(num) for num in range(1, 4 + 1)],
            "mode": ["temperature", "top_p"],
            "model": ["text-davinci-003", "text-curie-001"],
            "low_usage_mode": ["True", "False"],
            "image_size": ["256x256", "512x512", "1024x1024"],
            "summarize_conversation": ["True", "False"],
            "welcome_message_enabled": ["True", "False"],
            "num_static_conversation_items": [str(num) for num in range(5, 20 + 1)],
            "num_conversation_lookback": [str(num) for num in range(5, 15 + 1)],
            "summarize_threshold": [str(num) for num in range(800, 3500, 50)],
            "type": ["warn", "delete"],
        }
        for parameter in values:
            if parameter == ctx.options["parameter"]:
                return [
                    value
                    for value in values[ctx.options["parameter"]]
                    if value.startswith(ctx.value.lower())
                ]
        await ctx.interaction.response.defer()  # defer so the autocomplete in int values doesn't error but rather just says not found
        return []

    async def get_value_moderations(
        ctx: discord.AutocompleteContext,
    ):  # Behaves a bit weird if you go back and edit the parameter without typing in a new command
        """gets valid values for the type option"""
        print(f"The value is {ctx.value}")
        return [
            value for value in ["warn", "delete"] if value.startswith(ctx.value.lower())
        ]

    async def get_value_alert_id_channel(self, ctx: discord.AutocompleteContext):
        """gets valid values for the channel option"""
        return [
            channel.name
            for channel in ctx.interaction.guild.channels
            if channel.name.startswith(ctx.value.lower())
        ]


class Translations_autocompleter:
    """autocompleter for the translations command"""

    async def get_languages(ctx: discord.AutocompleteContext):
        """gets valid values for the language option"""
        return [
            language
            for language in TranslationModel.get_all_country_names()
            if language.lower().startswith(ctx.value.lower())
        ]

    async def get_formality_values(self, ctx: discord.AutocompleteContext):
        """gets valid values for the formality option"""
        return [
            value
            for value in ["prefer_more", "prefer_less"]
            if value.lower().startswith(ctx.value.lower())
        ]


class File_autocompleter:
    """Autocompleter for the opener command"""

    async def get_openers(ctx: discord.AutocompleteContext):
        """get all files in the openers folder"""
        try:
            return [
                file
                for file in os.listdir(EnvService.find_shared_file("openers"))
                if file.startswith(ctx.value.lower())
            ][
                :25
            ]  # returns the 25 first files from your current input
        except Exception:
            return ["No 'openers' folder"]
