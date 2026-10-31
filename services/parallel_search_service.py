"""Anonymous Parallel Search MCP source lookups for the base /web command."""

import asyncio
import json

import aiohttp
import discord

from services.environment_service import EnvService
from services.responses_service import (
    Citation, ResponsesError, SearchResponse, _citation_url,
)

MCP_URL = "https://search.parallel.ai/mcp"
USER_AGENT = "GPTDiscord/13.2.6"
MAX_RESPONSE_BYTES = 1_000_000
PROTOCOL_VERSION = "2025-03-26"


def _rpc_result(payload, request_id):
    if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0" or payload.get("id") != request_id:
        raise ResponsesError("Parallel returned an invalid search response.")
    if payload.get("error") or not isinstance(payload.get("result"), dict):
        raise ResponsesError("Parallel could not complete the search. Try again later.")
    return payload["result"]


async def _read_result(response, request_id):
    if response.content_type == "text/event-stream":
        size, lines = 0, []
        async for raw in response.content:
            size += len(raw)
            if size > MAX_RESPONSE_BYTES:
                raise ResponsesError("Parallel returned too much search data.")
            line = raw.decode("utf-8").rstrip("\r\n")
            if line.startswith("data:"):
                lines.append(line[5:].lstrip(" "))
            elif not line and lines:
                payload = json.loads("\n".join(lines))
                lines = []
                if isinstance(payload, dict) and payload.get("id") == request_id:
                    return _rpc_result(payload, request_id)
        raise ResponsesError("Parallel returned an incomplete search response.")
    chunks, size = [], 0
    async for chunk in response.content.iter_chunked(65536):
        size += len(chunk)
        if size > MAX_RESPONSE_BYTES:
            raise ResponsesError("Parallel returned too much search data.")
        chunks.append(chunk)
    return _rpc_result(json.loads(b"".join(chunks)), request_id)


def _search_response(result):
    if result.get("isError"):
        # Never echo server diagnostics or instructions into a guild.
        raise ResponsesError("Parallel could not complete the search. Try again later.")
    data = result.get("structuredContent")
    if not isinstance(data, dict):
        for block in result.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                try:
                    candidate = json.loads(block.get("text", ""))
                except (TypeError, ValueError):
                    continue
                if isinstance(candidate, dict) and "results" in candidate:
                    data = candidate
                    break
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise ResponsesError("Parallel returned an invalid search response.")
    citations, excerpts, seen = [], [], set()
    for item in data["results"]:
        if not isinstance(item, dict):
            continue
        url = _citation_url(item.get("url"))
        if not url or url in seen:
            continue
        seen.add(url)
        title = item.get("title")
        title = title if isinstance(title, str) and title.strip() else url
        citations.append(Citation(title, url))
        parts = item.get("excerpts") or []
        if not isinstance(parts, list):
            raise ResponsesError("Parallel returned an invalid search response.")
        # Excerpts are untrusted source text, not bot-authored answers or links.
        text = "\n".join(p for p in parts if isinstance(p, str))[:1800]
        excerpts.append(
            f"**Source {len(citations)}**\n"
            + discord.utils.escape_markdown(text or "No excerpt available.")
        )
        if len(citations) == 5:
            break
    return SearchResponse(
        text=("Web source excerpts from Parallel Search:\n\n" + "\n\n".join(excerpts))
        if excerpts else "Parallel found no web sources for this question.",
        citations=tuple(citations), model="", usage={}, search_calls=1,
    )


class ParallelSearchService:
    """One bounded MCP connection per standalone search; no stored credentials."""

    async def search(self, question, *, session_id):
        question = question.strip()
        if not question:
            raise ValueError("Enter a question to search for.")
        if len(question) > 6000:
            raise ValueError("Keep search questions under 6,000 characters.")
        headers = {
            "User-Agent": "GPTDiscord/13.2.6",
            "Accept": "application/json, text/event-stream",
        }
        try:
            async with asyncio.timeout(45):
                # No netrc or ambient Authorization headers. Honor only the
                # project's explicit environment-proxy resolver.
                async with aiohttp.ClientSession(
                    headers=headers, trust_env=False,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as client:
                    async def request(method, params=None, request_id=None):
                        payload = {"jsonrpc": "2.0", "method": method}
                        if params is not None:
                            payload["params"] = params
                        if request_id is not None:
                            payload["id"] = request_id
                        async with client.post(
                            MCP_URL, json=payload, headers=headers,
                            proxy=EnvService.get_http_proxy(MCP_URL),
                            allow_redirects=False,
                        ) as response:
                            if response.status == 429:
                                raise ResponsesError(
                                    "Parallel's free search limit was reached. Try again later."
                                )
                            if response.status not in {200, 202}:
                                raise ResponsesError(
                                    "Parallel could not complete the search. Try again later."
                                )
                            if request_id is None:
                                return None
                            result = await _read_result(response, request_id)
                            if method == "initialize":
                                transport_id = response.headers.get("Mcp-Session-Id")
                                if transport_id:
                                    headers["Mcp-Session-Id"] = transport_id
                            return result

                    initialized = await request("initialize", {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "GPTDiscord", "version": "13.2.6"},
                    }, 1)
                    if initialized.get("protocolVersion") != PROTOCOL_VERSION:
                        raise ResponsesError("Parallel uses an unsupported MCP protocol.")
                    headers["MCP-Protocol-Version"] = PROTOCOL_VERSION
                    await request("notifications/initialized")
                    tools = await request("tools/list", request_id=2)
                    if not any(
                        isinstance(tool, dict) and tool.get("name") == "web_search"
                        for tool in tools.get("tools", [])
                    ):
                        raise ResponsesError("Parallel's web search tool is unavailable.")
                    result = await request("tools/call", {
                        "name": "web_search",
                        "arguments": {
                            "objective": question, "search_queries": [question],
                            "session_id": session_id,
                        },
                    }, 3)
                    return _search_response(result)
        except (TimeoutError, aiohttp.ClientError):
            raise ResponsesError(
                "Could not reach Parallel search. Please try again shortly."
            ) from None
        except (ValueError, TypeError, UnicodeError, AttributeError):
            raise ResponsesError("Parallel returned an invalid search response.") from None
