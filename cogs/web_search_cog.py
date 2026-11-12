"""Selectable source lookups or synthesized web answers in the base install."""

import io
import logging
import uuid

import discord
from openai import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
)

from models.openai_model import Models
from services.environment_service import EnvService
from services.access_service import has_any_role
from services.keenable_search_service import KeenableSearchService
from services.parallel_search_service import ParallelSearchService
from services.responses_service import (
    ResponsesError,
    ResponsesService,
    discord_messages,
)

logger = logging.getLogger(__name__)


def configured_web_models():
    configured = EnvService.get_openai_model()
    models = ([configured] if configured else []) + Models.WEB_SEARCH_MODELS
    return list(dict.fromkeys(models))


async def web_models(ctx):
    return [
        model for model in configured_web_models() if model.startswith(ctx.value or "")
    ][:25]


class WebSearchCog(discord.Cog):
    def __init__(
        self, bot, usage_service=None, responses_service=None, parallel_service=None,
        keenable_service=None,
    ):
        self.bot = bot
        self.usage_service = usage_service
        self.responses_service = responses_service or ResponsesService()
        self.parallel_service = parallel_service or ParallelSearchService()
        self.keenable_service = keenable_service or KeenableSearchService()
        self.search_roles = EnvService.get_search_roles()

    @discord.slash_command(
        name="web",
        description="Look up web sources with Parallel or Keenable, or get a paid OpenAI answer.",
        guild_ids=EnvService.get_allowed_guilds(),
    )
    @discord.option(
        "question", str, description="What would you like to look up?", max_length=6000
    )
    @discord.option(
        "model",
        str,
        description="OpenAI model; choosing a model selects paid OpenAI search",
        default=None,
        autocomplete=web_models,
    )
    @discord.option(
        "provider", str,
        description="Parallel or Keenable source excerpts (free) or an OpenAI answer (paid)",
        choices=["parallel", "keenable", "openai"], default=None,
    )
    async def web(
        self, ctx: discord.ApplicationContext, question: str, model: str = None,
        provider: str = None,
    ):
        allowed_mentions = discord.AllowedMentions.none()

        async def refuse(message):
            await ctx.respond(message, ephemeral=True, allowed_mentions=allowed_mentions)

        if not has_any_role(ctx, self.search_roles):
            return await refuse(
                "You need a configured SEARCH_ROLES role to use web search."
            )
        try:
            configured_provider = EnvService.get_web_search_provider()
        except ValueError as exc:
            return await refuse(str(exc))
        if configured_provider == "disabled":
            return await refuse("Web search is disabled on this bot.")
        if provider not in {None, "parallel", "keenable", "openai"}:
            return await refuse("Choose Parallel, Keenable, or OpenAI for web search.")
        if provider in {"parallel", "keenable"} and model is not None:
            return await refuse(
                "The model option applies to OpenAI answers."
                " Omit it for Parallel or Keenable source excerpts."
            )
        # An explicit model continues to mean an OpenAI request. Auto preserves
        # applicable existing credentials and configured model choices.
        selected = provider or ("openai" if model is not None else configured_provider)
        api_key = None
        personal_keys = EnvService.get_user_input_api_keys()
        if selected not in {"parallel", "keenable"} and personal_keys:
            database = EnvService.get_api_db()
            try:
                api_key = database.get(ctx.user.id)
            finally:
                database.close()
        if selected == "auto":
            configured_key = api_key if personal_keys else EnvService.get_openai_token()
            has_openai_setup = configured_key or EnvService.get_openai_model()
            selected = "openai" if has_openai_setup else "parallel"
        if selected == "openai":
            model = model or EnvService.get_openai_model() or Models.DEFAULT
            if model not in configured_web_models():
                return await refuse(
                    "Choose a supported web search model from the model suggestions."
                )
            if personal_keys and not api_key:
                return await refuse(
                    "Run `/setup` to configure your OpenAI API key before using web search."
                )
        await ctx.defer()
        try:
            if selected == "parallel":
                # /web has no persistent conversation or follow-up controls:
                # each invocation owns one new research-run identity.
                result = await self.parallel_service.search(
                    question, session_id=uuid.uuid4().hex
                )
            elif selected == "keenable":
                result = await self.keenable_service.search(question)
            else:
                result = await self.responses_service.search(
                    question, model=model, api_key=api_key
                )
            if self.usage_service and result.usage:
                try:
                    await self.usage_service.update_usage_from_response(
                        result.usage, result.model or model
                    )
                except Exception as exc:
                    logger.error(
                        "Could not record web search token usage (%s)",
                        type(exc).__name__,
                    )
            try:
                messages = discord_messages(result)
            except ValueError:
                messages = []
            if not messages or len(messages) > 6:
                sources = "\n".join(
                    f"{source.title}: {source.url}" for source in result.citations
                )
                file = discord.File(
                    io.BytesIO((result.text + "\n\nSources\n" + sources).encode()),
                    filename="web-search.txt",
                )
                await ctx.respond(
                    "The complete answer and sources are attached.",
                    file=file,
                    allowed_mentions=allowed_mentions,
                )
            else:
                for message in messages:
                    await ctx.respond(message, allowed_mentions=allowed_mentions)
            # API token usage is measured; hosted search fees are separate and
            # deliberately not presented as included in the local token estimate.
        except AuthenticationError:
            await ctx.respond(
                "OpenAI rejected the configured API key. Check the key and project access.",
                ephemeral=True,
                allowed_mentions=allowed_mentions,
            )
        except RateLimitError:
            await ctx.respond(
                "OpenAI's rate or spending limit was reached. Try again later or check billing.",
                ephemeral=True,
                allowed_mentions=allowed_mentions,
            )
        except BadRequestError:
            await ctx.respond(
                "OpenAI rejected this web search request. Check that the selected model and API endpoint support Responses web search.",
                ephemeral=True,
                allowed_mentions=allowed_mentions,
            )
        except APIConnectionError:
            await ctx.respond(
                "Could not reach OpenAI. Please try again shortly.",
                ephemeral=True,
                allowed_mentions=allowed_mentions,
            )
        except APIStatusError:
            await ctx.respond(
                "OpenAI could not complete the search. Please try again shortly.",
                ephemeral=True,
                allowed_mentions=allowed_mentions,
            )
        except ResponsesError as exc:
            await ctx.respond(
                str(exc), ephemeral=True, allowed_mentions=allowed_mentions
            )
        except ValueError as exc:
            # Only local input validation errors are shown; API errors above can
            # contain account details and are never echoed into a guild channel.
            await ctx.respond(
                str(exc), ephemeral=True, allowed_mentions=allowed_mentions
            )
        except Exception as exc:
            logger.error("Web search failed (%s)", type(exc).__name__)
            await ctx.respond(
                "The web search could not be completed. Please try again shortly.",
                ephemeral=True,
                allowed_mentions=allowed_mentions,
            )
