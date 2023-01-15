import discord

from services.environment_service import EnvService
from typing import Callable

ADMIN_ROLES = EnvService.get_admin_roles()
DALLE_ROLES = EnvService.get_dalle_roles()
GPT_ROLES = EnvService.get_gpt_roles()
ALLOWED_GUILDS = EnvService.get_allowed_guilds()


class Check:
    def check_admin_roles(self) -> Callable:
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

    def check_dalle_roles(self) -> Callable:
        async def inner(ctx: discord.ApplicationContext):
            if DALLE_ROLES == [None]:
                return True
            if not any(role.name.lower() in DALLE_ROLES for role in ctx.user.roles):
                await ctx.defer(ephemeral=True)
                await ctx.respond(
                    "You don't have permission, list of roles is {DALLE_ROLES}",
                    ephemeral=True,
                    delete_after=10,
                )
                return False
            return True

        return inner

    def check_gpt_roles(self) -> Callable:
        async def inner(ctx: discord.ApplicationContext):
            if GPT_ROLES == [None]:
                return True
            if not any(role.name.lower() in GPT_ROLES for role in ctx.user.roles):
                await ctx.defer(ephemeral=True)
                await ctx.respond(
                    "You don't have permission, list of roles is {GPT_ROLES}",
                    ephemeral=True,
                    delete_after=10,
                )
                return False
            return True

        return inner
