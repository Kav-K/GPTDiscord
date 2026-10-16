import discord
import re
import aiohttp

from services.environment_service import EnvService
from services.access_service import has_any_role
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
    def _role_check(configured):
        async def inner(ctx: discord.ApplicationContext):
            roles = configured()
            if has_any_role(ctx, roles):
                return True
            labels = [
                str(role).strip()
                for role in roles
                if role is not None and str(role).strip()
            ]
            labels = [f"<@&{role}>" if role.isdecimal() else role for role in labels]
            await ctx.respond(
                "You don't have permission. Required roles: " + ", ".join(labels),
                ephemeral=True,
                delete_after=10,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return False

        return inner

    @staticmethod
    def check_admin_roles() -> Callable:
        return Check._role_check(lambda: ADMIN_ROLES)

    @staticmethod
    def check_dalle_roles() -> Callable:
        return Check._role_check(lambda: DALLE_ROLES)

    @staticmethod
    def check_gpt_roles() -> Callable:
        return Check._role_check(lambda: GPT_ROLES)

    @staticmethod
    def check_index_roles() -> Callable:
        return Check._role_check(lambda: INDEX_ROLES)

    @staticmethod
    def check_translator_roles() -> Callable:
        return Check._role_check(lambda: TRANSLATOR_ROLES)

    @staticmethod
    def check_search_roles() -> Callable:
        return Check._role_check(lambda: SEARCH_ROLES)


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
