import traceback

import aiohttp
import discord

from models.deepl_model import TranslationModel
from models.search_model import Search
from services.environment_service import EnvService


ALLOWED_GUILDS = EnvService.get_allowed_guilds()


class SearchService(discord.Cog, name="SearchService"):
    """Cog containing translation commands and retrieval of translation services"""

    def __init__(
        self,
        bot,
        gpt_model,
        pinecone_service,
    ):
        super().__init__()
        self.bot = bot
        self.model = Search(gpt_model, pinecone_service)
        # Make a mapping of all the country codes and their full country names:

    async def search_command(self, ctx, query):
        """Command handler for the translation command"""
        await ctx.defer()
        await self.model.search(query)
        await ctx.respond("ok")
