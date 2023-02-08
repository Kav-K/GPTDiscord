import traceback

import aiohttp
import re
import discord
from discord.ext import pages

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
        usage_service,
    ):
        super().__init__()
        self.bot = bot
        self.usage_service = usage_service
        self.model = Search(gpt_model, usage_service)
        self.EMBED_CUTOFF = 2000
        # Make a mapping of all the country codes and their full country names:

    async def paginate_embed(self, response_text):
        """Given a response text make embed pages and return a list of the pages. Codex makes it a codeblock in the embed"""

        response_text = [
            response_text[i : i + self.EMBED_CUTOFF]
            for i in range(0, len(response_text), self.EMBED_CUTOFF)
        ]
        pages = []
        first = False
        # Send each chunk as a message
        for count, chunk in enumerate(response_text, start=1):
            if not first:
                page = discord.Embed(
                    title=f"Page {count}",
                    description=chunk,
                )
                first = True
            else:
                page = discord.Embed(
                    title=f"Search Results",
                    description=chunk,
                )
            pages.append(page)

        return pages

    async def search_command(
        self, ctx: discord.ApplicationContext, query, search_scope, nodes
    ):
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
            await ctx.respond("The search service is not enabled.")
            return

        await ctx.defer()

        try:
            response = await self.model.search(ctx, query, user_api_key, search_scope, nodes)
        except ValueError:
            await ctx.respond(
                "The Google Search API returned an error. Check the console for more details.",
                ephemeral=True,
            )
            return
        except Exception:
            await ctx.respond(
                "An error occurred. Check the console for more details.", ephemeral=True
            )
            traceback.print_exc()
            return

        url_extract_pattern = "https?:\\/\\/(?:www\\.)?[-a-zA-Z0-9@:%._\\+~#=]{1,256}\\.[a-zA-Z0-9()]{1,6}\\b(?:[-a-zA-Z0-9()@:%_\\+.~#?&\\/=]*)"
        urls = re.findall(
            url_extract_pattern,
            str(response.get_formatted_sources(length=200)),
            flags=re.IGNORECASE,
        )
        urls = "\n".join(f"<{url}>" for url in urls)

        query_response_message = f"**Query:**`\n\n{query.strip()}`\n\n**Query response:**\n\n{response.response.strip()}\n\n**Sources:**\n{urls}"
        query_response_message = query_response_message.replace(
            "<|endofstatement|>", ""
        )

        # If the response is too long, lets paginate using the discord pagination
        # helper
        embed_pages = await self.paginate_embed(query_response_message)
        paginator = pages.Paginator(
            pages=embed_pages,
            timeout=None,
            author_check=False,
        )

        await paginator.respond(ctx.interaction)
