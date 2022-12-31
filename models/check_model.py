import discord
from models.env_service_model import EnvService
from typing import Callable


ALLOWED_ROLES = EnvService.get_allowed_roles()


class Check:
    def check_valid_roles() -> Callable:
        async def inner(ctx: discord.ApplicationContext):
            if not any(role.name in ALLOWED_ROLES for role in ctx.user.roles):
                await ctx.defer(ephemeral=True)
                await ctx.send_followup(
                    "You don't have permission to use this.",
                    ephemeral=True,
                    delete_after=10,
                )
                return False
            return True

        return inner
