"""Outcrawl Search source lookups for the base /web command."""

import asyncio
import json

import aiohttp
import discord

from services.environment_service import EnvService
from services.responses_service import (
    Citation, ResponsesError, SearchResponse, _citation_url,
)

SEARCH_URL = "https://api.outcrawl.ai/v1/search"
USER_AGENT = "GPTDiscord/13.2.6"
MAX_RESPONSE_BYTES = 1_000_000
MAX_SOURCES = 5


def _search_response(data):
    if not isinstance(data, dict) or not isinstance(data.get("hits"), list):
        raise ResponsesError("Outcrawl returned an invalid search response.")
    citations, excerpts, seen = [], [], set()
    for item in data["hits"]:
        if not isinstance(item, dict):
            continue
        url = _citation_url(item.get("url"))
        if not url or url in seen:
            continue
        seen.add(url)
        title = item.get("title")
        title = title if isinstance(title, str) and title.strip() else url
        citations.append(Citation(title, url))
        snippet = item.get("snippet")
        # Snippets are untrusted source text, not bot-authored answers or links.
        text = snippet[:1800] if isinstance(snippet, str) else ""
        excerpts.append(
            f"**Source {len(citations)}**\n"
            + discord.utils.escape_markdown(text or "No excerpt available.")
        )
        if len(citations) == MAX_SOURCES:
            break
    return SearchResponse(
        text=("Web source excerpts from Outcrawl Search:\n\n" + "\n\n".join(excerpts))
        if excerpts else "Outcrawl found no web sources for this question.",
        citations=tuple(citations), model="", usage={}, search_calls=1,
    )


class OutcrawlSearchService:
    """One bounded REST request per standalone search with the bot's Outcrawl key."""

    async def search(self, question, *, api_key):
        question = question.strip()
        if not question:
            raise ValueError("Enter a question to search for.")
        if len(question) > 6000:
            raise ValueError("Keep search questions under 6,000 characters.")
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        try:
            async with asyncio.timeout(45):
                # No netrc or ambient Authorization headers. Honor only the
                # project's explicit environment-proxy resolver.
                async with aiohttp.ClientSession(
                    trust_env=False, timeout=aiohttp.ClientTimeout(total=30),
                ) as client:
                    async with client.post(
                        SEARCH_URL, json={"query": question, "limit": MAX_SOURCES},
                        headers=headers, proxy=EnvService.get_http_proxy(SEARCH_URL),
                        allow_redirects=False,
                    ) as response:
                        if response.status in {401, 403}:
                            raise ResponsesError(
                                "Outcrawl rejected the configured OUTCRAWL_API_KEY."
                            )
                        if response.status == 402:
                            raise ResponsesError(
                                "The Outcrawl account for this bot is out of credits."
                            )
                        if response.status == 429:
                            raise ResponsesError(
                                "Outcrawl's rate limit was reached. Try again later."
                            )
                        if response.status != 200:
                            raise ResponsesError(
                                "Outcrawl could not complete the search. Try again later."
                            )
                        chunks, size = [], 0
                        async for chunk in response.content.iter_chunked(65536):
                            size += len(chunk)
                            if size > MAX_RESPONSE_BYTES:
                                raise ResponsesError("Outcrawl returned too much search data.")
                            chunks.append(chunk)
                        return _search_response(json.loads(b"".join(chunks)))
        except (TimeoutError, aiohttp.ClientError):
            raise ResponsesError(
                "Could not reach Outcrawl search. Please try again shortly."
            ) from None
        except (ValueError, TypeError, UnicodeError, AttributeError):
            raise ResponsesError("Outcrawl returned an invalid search response.") from None
