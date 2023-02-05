import traceback

import aiohttp
import discord

from models.deepl_model import TranslationModel
from models.search_model import Search
from services.environment_service import EnvService
from services.text_service import TextService

ALLOWED_GUILDS = EnvService.get_allowed_guilds()
USER_INPUT_API_KEYS = EnvService.get_user_input_api_keys()
USER_KEY_DB = EnvService.get_api_db()


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

    async def search_command(self, ctx, query, search_scope):
        """Command handler for the translation command"""
        user_api_key = None
        if USER_INPUT_API_KEYS:
            user_api_key = await TextService.get_user_api_key(
                ctx.user.id, ctx, USER_KEY_DB
            )
            if not user_api_key:
                return

        if (
            not EnvService.get_google_search_api_key()
            or not EnvService.get_google_search_engine_id()
        ):
            await ctx.send("The search service is not enabled.")
            return

        await ctx.defer()

        response = await self.model.search(query, user_api_key, search_scope)

        await ctx.respond(
            f"**Query:**\n\n{query.strip()}\n\n**Query response:**\n\n{response.response.strip()}"
        )
