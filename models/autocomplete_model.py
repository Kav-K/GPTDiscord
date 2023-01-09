from pathlib import Path
import os
import re

import discord
from models.usage_service_model import UsageService
from models.openai_model import Model

usage_service = UsageService(Path(os.environ.get("DATA_DIR", os.getcwd())))
model = Model(usage_service)


class Settings_autocompleter:
    async def get_settings(ctx: discord.AutocompleteContext):
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
        values = {
            "max_conversation_length": [str(num) for num in range(1,500,2)],
            "num_images": [str(num) for num in range(1,4+1)],
            "mode": ["temperature", "top_p"],
            "model": ["text-davinci-003", "text-curie-001"],
            "low_usage_mode": ["True", "False"],
            "image_size": ["256x256", "512x512", "1024x1024"],
            "summarize_conversation": ["True", "False"],
            "welcome_message_enabled": ["True", "False"],
            "num_static_conversation_items": [str(num) for num in range(5,20+1)],
            "num_conversation_lookback": [str(num) for num in range(5,15+1)],
            "summarize_threshold": [str(num) for num in range(800, 3500, 50)]
        }
        if ctx.options["parameter"] in values.keys():
            return [value for value in values[ctx.options["parameter"]]]
        else:
            await ctx.interaction.response.defer()  # defer so the autocomplete in int values doesn't error but rather just says not found
            return []


class File_autocompleter:
    async def get_openers(ctx: discord.AutocompleteContext):
        try:
            return [
                file
                for file in os.listdir("openers")
                if file.startswith(ctx.value.lower())
            ][
                :25
            ]  # returns the 25 first files from your current input
        except:
            return ["No 'openers' folder"]
