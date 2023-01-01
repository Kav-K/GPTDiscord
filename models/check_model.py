import discord

from models.env_service_model import EnvService
from typing import Callable

ADMIN_ROLES = EnvService.get_admin_roles()
DALLE_ROLES = EnvService.get_dalle_roles()
GPT_ROLES = EnvService.get_gpt_roles()
ALLOWED_GUILDS = EnvService.get_allowed_guilds()


class Check:
    def check_admin_roles() -> Callable:
        async def inner(ctx: discord.ApplicationContext):
            if not any(role.name.lower() in ADMIN_ROLES for role in ctx.user.roles):
                await ctx.defer(ephemeral=True)
                await ctx.respond(
                    "You don't have admin permission to use this.",
                    ephemeral=True,
                    delete_after=10,
                )
                return False
            return True
        return inner

    def check_dalle_roles() -> Callable:
        async def inner(ctx: discord.ApplicationContext):
            if not any(role.name.lower() in DALLE_ROLES for role in ctx.user.roles):
                await ctx.defer(ephemeral=True)
                await ctx.respond(
                    "You don't have dalle permission to use this.",
                    ephemeral=True,
                    delete_after=10,
                )
                return False
            return True
        return inner

    def check_gpt_roles() -> Callable:
        async def inner(ctx: discord.ApplicationContext):
            if not any(role.name.lower() in GPT_ROLES for role in ctx.user.roles):
                await ctx.defer(ephemeral=True)
                await ctx.respond(
                    "You don't have gpt permission to use this.",
                    ephemeral=True,
                    delete_after=10,
                )
                return False
            return True
        return inner