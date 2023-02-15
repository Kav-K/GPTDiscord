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

class RedoSearchUser:
    def __init__(self, ctx, query, search_scope, nodes):
        self.ctx = ctx
        self.query = query
        self.search_scope = search_scope
        self.nodes = nodes

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
        self.redo_users = {}
        # Make a mapping of all the country codes and their full country names:

    async def paginate_embed(self, response_text, user: discord.Member):
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
                    title=f"Search Results",
                    description=chunk,
                )
                first = True
            else:
                page = discord.Embed(
                    title=f"Page {count}",
                    description=chunk,
                )
            page.set_footer(text=f"Requested by {user.name}", icon_url=user.avatar.url)
            pages.append(page)

        return pages

    async def search_command(
        self, ctx: discord.ApplicationContext, query, search_scope, nodes, redo=None
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

        await ctx.defer() if not redo else None

        try:
            response, refined_text = await self.model.search(
                ctx, query, user_api_key, search_scope, nodes
            )
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

        query_response_message = f"**Question:**\n\n`{query.strip()}`\n\n**Google Search Query**\n\n`{refined_text.strip()}`\n\n**Final Answer:**\n\n{response.response.strip()}\n\n**Sources:**\n{urls}"
        query_response_message = query_response_message.replace(
            "<|endofstatement|>", ""
        )

        # If the response is too long, lets paginate using the discord pagination
        # helper
        embed_pages = await self.paginate_embed(query_response_message, ctx.user)
        paginator = pages.Paginator(
            pages=embed_pages,
            timeout=None,
            author_check=False,
            custom_view=RedoButton(ctx, self),
        )

        self.redo_users[ctx.user.id] = RedoSearchUser(ctx, query, search_scope, nodes)

        await paginator.respond(ctx.interaction)


# A view for a redo button
class RedoButton(discord.ui.View):
    def __init__(self, ctx: discord.ApplicationContext, search_cog):
        super().__init__()
        self.ctx = ctx
        self.search_cog = search_cog

    @discord.ui.button(label="Redo", style=discord.ButtonStyle.danger)
    async def redo(self, button: discord.ui.Button, interaction: discord.Interaction):
        """Redo the translation"""
        await interaction.response.send_message("Redoing search...", ephemeral=True, delete_after=15)
        await self.search_cog.search_command(self.search_cog.redo_users[self.ctx.user.id].ctx, self.search_cog.redo_users[self.ctx.user.id].query, self.search_cog.redo_users[self.ctx.user.id].search_scope, self.search_cog.redo_users[self.ctx.user.id].nodes, redo=True)
