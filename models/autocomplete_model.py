from pathlib import Path
import os

import discord

from models.deepl_model import TranslationModel
from services.moderations_service import ModerationOptions
from services.usage_service import UsageService
from models.openai_model import ImageSize, Model, ModelLimits, Models
from services.environment_service import EnvService
from utils.model_settings import editable_model_settings

usage_service = UsageService(Path(os.environ.get("DATA_DIR", os.getcwd())))
model = Model(usage_service)


def _matching_choices(ctx, values):
    """Discord accepts at most 25 autocomplete results."""
    prefix = (ctx.value or "").lower()
    return [
        value for value in dict.fromkeys(values) if value.lower().startswith(prefix)
    ][:25]


class Settings_autocompleter:
    """autocompleter for the settings command"""

    async def get_settings(ctx: discord.AutocompleteContext):
        """get settings for the settings option"""
        SETTINGS = editable_model_settings(Model)
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
            "model": Models.TEXT_MODELS,
            "low_usage_mode": ["True", "False"],
            "image_size": ImageSize.ALL_SIZES,
            "summarize_conversations": ["True", "False"],
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
            return _matching_choices(ctx, options)

        # Autocomplete cannot defer. Pycord acknowledges the returned choices.
        return []

    async def get_function_calling_models(ctx: discord.AutocompleteContext):
        return _matching_choices(ctx, Models.TEXT_MODELS)

    async def get_models(
        ctx: discord.AutocompleteContext,
    ):
        """Gets all models"""
        return _matching_choices(ctx, Models.TEXT_MODELS)

    async def get_index_and_search_models(
        ctx: discord.AutocompleteContext,
    ):
        return _matching_choices(ctx, Models.TEXT_MODELS)

    async def get_converse_models(
        ctx: discord.AutocompleteContext,
    ):
        """Gets all models"""
        return _matching_choices(ctx, Models.TEXT_MODELS)

    async def get_dalle3_image_qualities(ctx: discord.AutocompleteContext):
        return _matching_choices(ctx, ["auto", "low", "medium", "high", "xhigh", "max"])

    async def get_dalle3_image_sizes(ctx: discord.AutocompleteContext):
        return _matching_choices(ctx, ["auto", "1024x1024", "1536x1024", "1024x1536"])

    async def get_dalle3_image_styles(ctx: discord.AutocompleteContext):
        return _matching_choices(ctx, ["natural", "vivid"])

    async def get_value_moderations(
        ctx: discord.AutocompleteContext,
    ):  # Behaves a bit weird if you go back and edit the parameter without typing in a new command
        """gets valid values for the type option"""
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
