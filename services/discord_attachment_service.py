"""Download Discord attachments through configured egress without API credentials."""

import aiofiles
import aiohttp
import discord

from services.environment_service import EnvService


async def read_attachment_bytes(attachment: discord.Attachment) -> bytes:
    url = getattr(attachment, "url", None)
    proxy = EnvService.get_http_proxy(url) if url else None
    if proxy is None:
        return await attachment.read()

    # Pycord's attachment download path ignores its bot-level proxy option.
    # Signed CDN URLs need no bot/OpenAI authorization or target-host netrc auth.
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=300), trust_env=False
    ) as session:
        async with session.get(url, proxy=proxy) as response:
            if response.status == 200:
                return await response.read()
            if response.status == 404:
                raise discord.NotFound(response, "attachment not found")
            if response.status == 403:
                raise discord.Forbidden(response, "cannot retrieve attachment")
            raise discord.HTTPException(response, "failed to get attachment")


async def save_attachment(attachment: discord.Attachment, destination) -> int:
    data = await read_attachment_bytes(attachment)
    async with aiofiles.open(destination, "wb") as output:
        return await output.write(data)
