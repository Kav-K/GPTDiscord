import discord
import re
import aiohttp

from services.environment_service import EnvService
from typing import Callable

ADMIN_ROLES = EnvService.get_admin_roles()
DALLE_ROLES = EnvService.get_dalle_roles()
GPT_ROLES = EnvService.get_gpt_roles()
INDEX_ROLES = EnvService.get_index_roles()
TRANSLATOR_ROLES = EnvService.get_translator_roles()
SEARCH_ROLES = EnvService.get_search_roles()
ALLOWED_GUILDS = EnvService.get_allowed_guilds()


class Check:
    @staticmethod
    def check_admin_roles() -> Callable:
        async def inner(ctx: discord.ApplicationContext):
            if ADMIN_ROLES == [None]:
                return True

            if not any(role.name.lower() in ADMIN_ROLES for role in ctx.user.roles):
                await ctx.defer(ephemeral=True)
                await ctx.respond(
                    f"You don't have permission, list of roles is {ADMIN_ROLES}",
                    ephemeral=True,
                    delete_after=10,
                )
                return False
            return True

        return inner

    @staticmethod
    def check_dalle_roles() -> Callable:
        async def inner(ctx: discord.ApplicationContext):
            if DALLE_ROLES == [None]:
                return True
            if not any(role.name.lower() in DALLE_ROLES for role in ctx.user.roles):
                await ctx.defer(ephemeral=True)
                await ctx.respond(
                    f"You don't have permission, list of roles is {DALLE_ROLES}",
                    ephemeral=True,
                    delete_after=10,
                )
                return False
            return True

        return inner

    @staticmethod
    def check_gpt_roles() -> Callable:
        async def inner(ctx: discord.ApplicationContext):
            if GPT_ROLES == [None]:
                return True
            if not any(role.name.lower() in GPT_ROLES for role in ctx.user.roles):
                await ctx.defer(ephemeral=True)
                await ctx.respond(
                    f"You don't have permission, list of roles is {GPT_ROLES}",
                    ephemeral=True,
                    delete_after=10,
                )
                return False
            return True

        return inner

    @staticmethod
    def check_index_roles() -> Callable:
        async def inner(ctx: discord.ApplicationContext):
            if INDEX_ROLES == [None]:
                return True
            if not any(role.name.lower() in INDEX_ROLES for role in ctx.user.roles):
                await ctx.defer(ephemeral=True)
                await ctx.respond(
                    f"You don't have permission, list of roles is {INDEX_ROLES}",
                    ephemeral=True,
                    delete_after=10,
                )
                return False
            return True

        return inner

    @staticmethod
    def check_translator_roles() -> Callable:
        async def inner(ctx: discord.ApplicationContext):
            if TRANSLATOR_ROLES == [None]:
                return True
            if not any(
                role.name.lower() in TRANSLATOR_ROLES for role in ctx.user.roles
            ):
                await ctx.defer(ephemeral=True)
                await ctx.respond(
                    f"You don't have permission, list of roles is {TRANSLATOR_ROLES}",
                    ephemeral=True,
                    delete_after=10,
                )
                return False
            return True

        return inner

    @staticmethod
    def check_search_roles() -> Callable:
        async def inner(ctx: discord.ApplicationContext):
            if SEARCH_ROLES == [None]:
                return True
            if not any(role.name.lower() in SEARCH_ROLES for role in ctx.user.roles):
                await ctx.defer(ephemeral=True)
                await ctx.respond(
                    f"You don't have permission, list of roles is {SEARCH_ROLES}",
                    ephemeral=True,
                    delete_after=10,
                )
                return False
            return True

        return inner


class UrlCheck:
    @staticmethod
    async def check_youtube_link(url):
        youtube_regex = (
            r"(https?://)?(www\.)?(youtube|youtu|youtube-nocookie)\.(com|be)/"
        )
        match = re.match(youtube_regex, url)
        if match is not None:
            return True

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                final_url = str(resp.url)
                match = re.match(youtube_regex, final_url)
                return match is not None
