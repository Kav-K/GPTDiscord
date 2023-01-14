import asyncio

import discord
from sqlitedict import SqliteDict

from services.environment_service import EnvService
from services.moderations_service import Moderation

MOD_DB = None
try:
    print("Attempting to retrieve the General and Moderations DB")
    MOD_DB = SqliteDict("main_db.sqlite", tablename="moderations", autocommit=True)
except Exception as e:
    print("Failed to retrieve the General and Moderations DB")
    raise e

class ModerationsService(discord.Cog, name="ModerationsService"):
    def __init__(
        self,
        bot,
        usage_service,
        model,
    ):
        super().__init__()
        self.bot = bot
        self.usage_service = usage_service
        self.model = model

        # Moderation service data
        self.moderation_queues = {}
        self.moderation_alerts_channel = EnvService.get_moderations_alert_channel()
        self.moderation_enabled_guilds = []
        self.moderation_tasks = {}
        self.moderations_launched = []
    @discord.Cog.listener()
    async def on_ready(self):
        # Check moderation service for each guild
        for guild in self.bot.guilds:
            await self.check_and_launch_moderations(guild.id)

    def check_guild_moderated(self, guild_id):
        return guild_id in MOD_DB and MOD_DB[guild_id]["moderated"]

    def get_moderated_alert_channel(self, guild_id):
        return MOD_DB[guild_id]["alert_channel"]

    def set_moderated_alert_channel(self, guild_id, channel_id):
        MOD_DB[guild_id] = {"moderated": True, "alert_channel": channel_id}
        MOD_DB.commit()

    def set_guild_moderated(self, guild_id, status=True):
        if guild_id not in MOD_DB:
            MOD_DB[guild_id] = {"moderated": status, "alert_channel": 0}
            MOD_DB.commit()
            return
        MOD_DB[guild_id] = {
            "moderated": status,
            "alert_channel": self.get_moderated_alert_channel(guild_id),
        }
        MOD_DB.commit()

    async def check_and_launch_moderations(self, guild_id, alert_channel_override=None):
        # Create the moderations service.
        print("Checking and attempting to launch moderations service...")
        if self.check_guild_moderated(guild_id):
            Moderation.moderation_queues[guild_id] = asyncio.Queue()

            moderations_channel = await self.bot.fetch_channel(
                self.get_moderated_alert_channel(guild_id)
                if not alert_channel_override
                else alert_channel_override
            )

            Moderation.moderation_tasks[guild_id] = asyncio.ensure_future(
                Moderation.process_moderation_queue(
                    Moderation.moderation_queues[guild_id], 1, 1, moderations_channel
                )
            )
            print("Launched the moderations service for guild " + str(guild_id))
            Moderation.moderations_launched.append(guild_id)
            return moderations_channel

        return None
    async def moderations_command(
        self, ctx: discord.ApplicationContext, status: str, alert_channel_id: str
    ):
        await ctx.defer()

        status = status.lower().strip()
        if status not in ["on", "off"]:
            await ctx.respond("Invalid status, please use on or off")
            return

        if status == "on":
            # Check if the current guild is already in the database and if so, if the moderations is on
            if self.check_guild_moderated(ctx.guild_id):
                await ctx.respond("Moderations is already enabled for this guild")
                return

            # Create the moderations service.
            self.set_guild_moderated(ctx.guild_id)
            moderations_channel = await self.check_and_launch_moderations(
                ctx.guild_id,
                Moderation.moderation_alerts_channel
                if not alert_channel_id
                else alert_channel_id,
            )
            self.set_moderated_alert_channel(ctx.guild_id, moderations_channel.id)

            await ctx.respond("Moderations service enabled")

        elif status == "off":
            # Cancel the moderations service.
            self.set_guild_moderated(ctx.guild_id, False)
            Moderation.moderation_tasks[ctx.guild_id].cancel()
            Moderation.moderation_tasks[ctx.guild_id] = None
            Moderation.moderation_queues[ctx.guild_id] = None
            Moderation.moderations_launched.remove(ctx.guild_id)
            await ctx.respond("Moderations service disabled")

    async def moderations_test_command(self, ctx: discord.ApplicationContext, prompt: str):
        await ctx.defer()
        response = await self.model.send_moderations_request(prompt)
        await ctx.respond(response["results"][0]["category_scores"])
        await ctx.send_followup(response["results"][0]["flagged"])

