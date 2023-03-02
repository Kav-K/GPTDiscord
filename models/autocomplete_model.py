from pathlib import Path
import os
import re

import discord

from models.deepl_model import TranslationModel
from services.moderations_service import ModerationOptions
from services.usage_service import UsageService
from models.openai_model import ImageSize, Model, ModelLimits, Models, Mode
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
            "max_conversation_length": [
                str(num)
                for num in range(
                    ModelLimits.MIN_CONVERSATION_LENGTH,
                    ModelLimits.MAX_CONVERSATION_LENGTH + 1,
                    2,
                )
            ],
            "num_images": [
                str(num)
                for num in range(
                    ModelLimits.MIN_NUM_IMAGES, ModelLimits.MAX_NUM_IMAGES + 1
                )
            ],
            "mode": Mode.ALL_MODES,
            "model": Models.TEXT_MODELS,
            "low_usage_mode": ["True", "False"],
            "image_size": ImageSize.ALL_SIZES,
            "summarize_conversation": ["True", "False"],
            "welcome_message_enabled": ["True", "False"],
            "num_static_conversation_items": [
                str(num)
                for num in range(
                    ModelLimits.MIN_NUM_STATIC_CONVERSATION_ITEMS,
                    ModelLimits.MAX_NUM_STATIC_CONVERSATION_ITEMS + 1,
                )
            ],
            "num_conversation_lookback": [
                str(num)
                for num in range(
                    ModelLimits.MIN_NUM_CONVERSATION_LOOKBACK,
                    ModelLimits.MAX_NUM_CONVERSATION_LOOKBACK + 1,
                )
            ],
            "summarize_threshold": [
                str(num)
                for num in range(
                    ModelLimits.MIN_SUMMARIZE_THRESHOLD,
                    ModelLimits.MAX_SUMMARIZE_THRESHOLD + 1,
                    50,
                )
            ],
            "type": ["warn", "delete"],
        }
        options = values.get(ctx.options["parameter"], [])
        if options:
            return [value for value in options if value.startswith(ctx.value.lower())]

        await ctx.interaction.response.defer()  # defer so the autocomplete in int values doesn't error but rather just says not found
        return []

    async def get_models(
        ctx: discord.AutocompleteContext,
    ):
        """Gets all models"""
        models = [
            value for value in Models.TEXT_MODELS if value.startswith(ctx.value.lower())
        ]
        return models

    async def get_converse_models(
        ctx: discord.AutocompleteContext,
    ):
        """Gets all models"""
        models = [
            value for value in Models.TEXT_MODELS if value.startswith(ctx.value.lower())
        ]
        models.append("chatgpt")

        # We won't let the user directly use these models but we will decide which one to use based on the status.
        attempt_removes = ["gpt-3.5-turbo", "gpt-3.5-turbo-0301"]

        for attempt_remove in attempt_removes:
            if attempt_remove in models:
                models.remove(attempt_remove)

        return models

    async def get_value_moderations(
        ctx: discord.AutocompleteContext,
    ):  # Behaves a bit weird if you go back and edit the parameter without typing in a new command
        """gets valid values for the type option"""
        print(f"The value is {ctx.value}")
        return [
            value
            for value in ModerationOptions.OPTIONS
            if value.startswith(ctx.value.lower())
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

    async def get_user_indexes(ctx: discord.AutocompleteContext):
        """get all files in the indexes folder"""
        try:
            return [
                file
                for file in os.listdir(
                    EnvService.find_shared_file(
                        f"indexes/{str(ctx.interaction.user.id)}/"
                    )
                )
                if file.startswith(ctx.value.lower())
            ][
                :25
            ]  # returns the 25 first files from your current input
        except Exception:
            return ["No user indexes found, add an index"]

    async def get_server_indexes(ctx: discord.AutocompleteContext):
        """get all files in the indexes folder"""
        try:
            return [
                file
                for file in os.listdir(
                    EnvService.find_shared_file(
                        f"indexes/{str(ctx.interaction.guild.id)}/"
                    )
                )
                if file.startswith(ctx.value.lower())
            ][
                :25
            ]  # returns the 25 first files from your current input
        except Exception:
            return ["No server indexes found, add an index"]

    async def get_user_search_indexes(ctx: discord.AutocompleteContext):
        """get all files in the indexes folder"""
        try:
            return [
                file
                for file in os.listdir(
                    EnvService.find_shared_file(
                        f"indexes/{str(ctx.interaction.user.id)}_search/"
                    )
                )
                if file.startswith(ctx.value.lower())
            ][
                :25
            ]  # returns the 25 first files from your current input
        except Exception:
            return ["No user indexes found, add an index"]
