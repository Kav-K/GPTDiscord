import traceback

import aiohttp
import re
import discord
from discord.ext import pages

from models.deepl_model import TranslationModel
from models.embed_statics_model import EmbedStatics
from models.search_model import Search
from services.environment_service import EnvService
from services.moderations_service import Moderation
from services.text_service import TextService

ALLOWED_GUILDS = EnvService.get_allowed_guilds()
USER_INPUT_API_KEYS = EnvService.get_user_input_api_keys()
USER_KEY_DB = EnvService.get_api_db()
PRE_MODERATE = EnvService.get_premoderate()


class RedoSearchUser:
    def __init__(self, ctx, query, search_scope, nodes, response_mode):
        self.ctx = ctx
        self.query = query
        self.search_scope = search_scope
        self.nodes = nodes
        self.response_mode = response_mode


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

    async def paginate_embed(
        self, response_text, user: discord.Member, original_link=None
    ):
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
                    title=f"Search Results"
                    if not original_link
                    else f"Follow-up results",
                    description=chunk,
                    url=original_link,
                )
                first = True
            else:
                page = discord.Embed(
                    title=f"Page {count}",
                    description=chunk,
                    url=original_link,
                )
            if user.avatar:
                page.set_footer(
                    text=f"Requested by {user.name}", icon_url=user.avatar.url
                )
            else:
                page.set_footer(
                    text=f"Requested by {user.name}", icon_url=user.default_avatar.url
                )
            pages.append(page)

        return pages

    async def search_command(
        self,
        ctx: discord.ApplicationContext,
        query,
        search_scope,
        nodes,
        deep,
        response_mode,
        redo=None,
        from_followup=None,
        followup_user=None,
    ):
        """Command handler for the search command"""
        await ctx.defer() if not redo else None

        # Check the opener for bad content.
        if PRE_MODERATE:
            if await Moderation.simple_moderate_and_respond(query, ctx):
                return

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
            await ctx.respond(
                embed=EmbedStatics.get_search_failure_embed(
                    str("The search service is not enabled on this server.")
                ),
            )
            return

        try:
            response, refined_text = await self.model.search(
                ctx, query, user_api_key, search_scope, nodes, deep, response_mode
            )
        except ValueError as e:
            traceback.print_exc()
            await ctx.respond(
                embed=EmbedStatics.get_search_failure_embed(str(e)),
                ephemeral=True,
            )
            return
        except Exception as e:
            await ctx.respond(
                embed=EmbedStatics.get_search_failure_embed(str(e)), ephemeral=True
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

        # Deduplicate the urls
        urls = "\n".join(dict.fromkeys(urls.split("\n")))

        if from_followup:
            original_link, followup_question = (
                from_followup.original_link,
                from_followup.followup_question,
            )
            query_response_message = f"**Question:**\n\n`{followup_question}`\n\n**Google Search Query**\n\n`{refined_text.strip()}`\n\n**Final Answer:**\n\n{response.response.strip()}\n\n**Sources:**\n{urls}"
        else:
            query_response_message = f"**Question:**\n\n`{query.strip()}`\n\n**Google Search Query**\n\n`{refined_text.strip()}`\n\n**Final Answer:**\n\n{response.response.strip()}\n\n**Sources:**\n{urls}"
        query_response_message = query_response_message.replace(
            "<|endofstatement|>", ""
        )
        query_response_message = query_response_message.replace(
            "Answer to original:\n", ""
        )
        query_response_message = query_response_message.replace(
            "Answer to follow-up:\n", ""
        )

        # If the response is too long, lets paginate using the discord pagination
        # helper
        embed_pages = await self.paginate_embed(
            query_response_message,
            ctx.user if not followup_user else followup_user,
            original_link if from_followup else None,
        )
        paginator = pages.Paginator(
            pages=embed_pages,
            timeout=None,
            author_check=False,
            custom_view=SearchView(ctx, self, query_response_message),
        )

        self.redo_users[ctx.user.id] = RedoSearchUser(
            ctx, query, search_scope, nodes, response_mode
        )

        await paginator.respond(ctx.interaction)


class SearchView(discord.ui.View):
    def __init__(
        self,
        ctx,
        search_cog,
        response_text,
    ):
        super().__init__(timeout=None)  # No timeout
        self.search_cog = search_cog
        self.ctx = ctx
        self.response_text = response_text
        self.add_item(RedoButton(self.ctx, self.search_cog))
        self.add_item(FollowupButton(self.ctx, self.search_cog, self.response_text))


# A view for a follow-up button
class FollowupButton(discord.ui.Button["SearchView"]):
    def __init__(self, ctx, search_cog, response_text):
        super().__init__(label="Follow Up", style=discord.ButtonStyle.green)
        self.search_cog = search_cog
        self.ctx = ctx
        self.response_text = response_text

    async def callback(self, interaction: discord.Interaction):
        """Send the followup modal"""
        await interaction.response.send_modal(
            modal=FollowupModal(self.ctx, self.search_cog, self.response_text)
        )


# A view for a redo button
class RedoButton(discord.ui.Button["SearchView"]):
    def __init__(self, ctx, search_cog):
        super().__init__(
            style=discord.ButtonStyle.danger,
            label="Redo",
            custom_id="redo_search_button",
        )
        self.ctx = ctx
        self.search_cog = search_cog

    async def callback(self, interaction: discord.Interaction):
        """Redo the search"""
        await interaction.response.send_message(
            embed=EmbedStatics.get_search_redo_progress_embed(),
            ephemeral=True,
            delete_after=15,
        )
        await self.search_cog.search_command(
            self.search_cog.redo_users[self.ctx.user.id].ctx,
            self.search_cog.redo_users[self.ctx.user.id].query,
            self.search_cog.redo_users[self.ctx.user.id].search_scope,
            self.search_cog.redo_users[self.ctx.user.id].nodes,
            deep=False,
            redo=True,
            response_mode=self.search_cog.redo_users[self.ctx.user.id].response_mode,
        )


class FollowupData:
    def __init__(self, original_link, followup_question):
        self.original_link = original_link
        self.followup_question = followup_question


# The modal for following up
class FollowupModal(discord.ui.Modal):
    def __init__(self, ctx, search_cog, response_text) -> None:
        super().__init__(title="Search Follow-up")
        # Get the argument named "user_key_db" and save it as USER_KEY_DB
        self.search_cog = search_cog
        self.ctx = ctx
        self.response_text = response_text

        self.add_item(
            discord.ui.InputText(
                label="What other questions do you have?",
                placeholder="",
            )
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        query = self.search_cog.redo_users[self.ctx.user.id].query

        # In the response text, get only the text between "**Final Answer:**" and "**Sources:**"
        self.response_text = self.response_text.split("**Final Answer:**")[1].split(
            "**Sources:**"
        )[0]

        # Build the context
        context_text = (
            "Original question: "
            + query
            + "\n"
            + "Answer to original: "
            + self.response_text
            + "\n"
            + "Follow-up question: "
            + self.children[0].value
        )

        # Get the link of the message that the user interacted on
        message_link = f"https://discord.com/channels/{interaction.guild_id}/{interaction.channel_id}/{interaction.message.id}"

        await self.search_cog.search_command(
            self.search_cog.redo_users[self.ctx.user.id].ctx,
            context_text,
            self.search_cog.redo_users[self.ctx.user.id].search_scope,
            self.search_cog.redo_users[self.ctx.user.id].nodes,
            deep=False,
            redo=True,
            from_followup=FollowupData(message_link, self.children[0].value),
            response_mode=self.search_cog.redo_users[self.ctx.user.id].response_mode,
            followup_user=interaction.user,
        )
