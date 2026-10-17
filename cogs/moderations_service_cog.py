import asyncio

import discord
from sqlitedict import SqliteDict

from services.environment_service import EnvService
from services.moderations_service import Moderation, ThresholdSet

MOD_DB = None
try:
    print("Attempting to retrieve the General and Moderations DB")
    MOD_DB = SqliteDict(
        EnvService.find_shared_file("main_db.sqlite"),
        tablename="moderations",
        autocommit=True,
    )
except Exception as e:
    print("Failed to retrieve the General and Moderations DB")
    raise e


class ModerationsService(discord.Cog, name="ModerationsService"):
    """Cog containing moderation tools and features"""

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

        # Defaults
        self.default_warn_set = ThresholdSet(0.01, 0.05, 0.05, 0.91, 0.1, 0.45, 0.1)
        self.default_delete_set = ThresholdSet(0.26, 0.26, 0.1, 0.95, 0.03, 0.85, 0.4)

    @discord.Cog.listener()
    async def on_ready(self):
        """Check moderation service for each guild"""
        for guild in self.bot.guilds:
            try:
                self.get_or_set_warn_set(guild.id)
                self.get_or_set_delete_set(guild.id)
                await self.check_and_launch_moderations(guild.id)
            except Exception as error:
                print(
                    f"Could not restore moderation for guild {guild.id} "
                    f"({type(error).__name__})"
                )
        print("The moderation service is ready.")

    def check_guild_moderated(self, guild_id):
        """Given guild id, return bool of moderation status"""
        return guild_id in MOD_DB and MOD_DB[guild_id]["moderated"]

    def get_moderated_alert_channel(self, guild_id):
        """Given guild id, return alert channel"""
        return MOD_DB[guild_id]["alert_channel"]

    def set_moderated_alert_channel(self, guild_id, channel_id):
        """Given guild id and channel id, set channel to recieve alerts"""
        MOD_DB[guild_id] = {"moderated": True, "alert_channel": channel_id}
        MOD_DB.commit()

    def get_or_set_warn_set(self, guild_id):
        """Get warn_set set for the guild, if not set them from default values"""
        guild_id = str(guild_id)
        key = guild_id + "_warn_set"
        if key not in MOD_DB:
            MOD_DB[key] = dict(
                zip(self.default_warn_set.keys, self.default_warn_set.thresholds)
            )
            MOD_DB.commit()
        return dict(MOD_DB[key])

    def get_or_set_delete_set(self, guild_id):
        """Get delete_set set for the guild, if not set them from default values"""
        guild_id = str(guild_id)
        key = guild_id + "_delete_set"
        if key not in MOD_DB:
            MOD_DB[key] = dict(
                zip(self.default_delete_set.keys, self.default_delete_set.thresholds)
            )
            MOD_DB.commit()
        return dict(MOD_DB[key])

    def set_warn_set(self, guild_id, threshold_set):
        """Set threshold for warning a message"""
        guild_id = str(guild_id)
        key = guild_id + "_warn_set"
        MOD_DB[key] = dict(zip(threshold_set.keys, threshold_set.thresholds))
        MOD_DB.commit()

    def set_delete_set(self, guild_id, threshold_set):
        """Set threshold for deleting a message"""
        guild_id = str(guild_id)
        key = guild_id + "_delete_set"
        MOD_DB[key] = dict(zip(threshold_set.keys, threshold_set.thresholds))
        MOD_DB.commit()

    def set_guild_moderated(self, guild_id, status=True):
        """Set the guild to moderated or not"""
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
        """Create the moderation service"""
        if self.check_guild_moderated(guild_id):
            moderations_channel = await self.bot.fetch_channel(
                self.get_moderated_alert_channel(guild_id)
                if not alert_channel_override
                else alert_channel_override
            )
            if (
                getattr(getattr(moderations_channel, "guild", None), "id", None)
                != guild_id
            ):
                raise ValueError(
                    "The moderation alert channel must belong to this guild"
                )
            if not self.check_guild_moderated(guild_id):
                return None
            existing = Moderation.moderation_tasks.get(guild_id)
            if existing is not None and not existing.done():
                return moderations_channel
            warn_set_nums = self.get_or_set_warn_set(guild_id).values()
            delete_set_nums = self.get_or_set_delete_set(guild_id).values()
            warn_set = ThresholdSet(*warn_set_nums)
            delete_set = ThresholdSet(*delete_set_nums)

            Moderation.moderation_queues[guild_id] = asyncio.Queue()
            Moderation.moderation_tasks[guild_id] = asyncio.create_task(
                Moderation.process_moderation_queue(
                    Moderation.moderation_queues[guild_id],
                    0.25,
                    0.25,
                    moderations_channel,
                    warn_set,
                    delete_set,
                )
            )
            print("Launched the moderations service for guild " + str(guild_id))
            if guild_id not in Moderation.moderations_launched:
                Moderation.moderations_launched.append(guild_id)
            return moderations_channel

        return None

    async def moderations_command(
        self, ctx: discord.ApplicationContext, status: str, alert_channel_id: str
    ):
        """command handler for toggling moderation and setting an alert channel"""
        await ctx.defer(ephemeral=True)

        try:
            if alert_channel_id:
                int(alert_channel_id)
        except ValueError:
            # the alert_channel_id was passed in as a channel NAME instead of an ID, fetch the ID.
            alert_channel = discord.utils.get(ctx.guild.channels, name=alert_channel_id)
            if alert_channel is None:
                await ctx.respond("The alert channel was not found.", ephemeral=True)
                return
            alert_channel_id = alert_channel.id

        if status == "on":
            # Check if the current guild is already in the database and if so, if the moderations is on
            if self.check_guild_moderated(ctx.guild_id):
                await ctx.respond("Moderations is already enabled for this guild")
                return

            # Create the moderations service.
            try:
                await self.start_moderations_service(
                    guild_id=ctx.guild_id, alert_channel_id=alert_channel_id
                )
            except (discord.HTTPException, ValueError):
                await ctx.respond(
                    "Choose an accessible moderation alert channel in this server.",
                    ephemeral=True,
                )
                return
            await ctx.respond("Moderations is now enabled for this guild")

        elif status == "off":
            # Cancel the moderations service.
            await self.stop_moderations_service(ctx.guild_id)
            await ctx.respond(
                "Moderations is now disabled for this guild", ephemeral=True
            )

    async def stop_moderations_service(self, guild_id):
        """Remove guild moderation status and stop the service"""
        self.set_guild_moderated(guild_id, False)
        task = Moderation.moderation_tasks.pop(guild_id, None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        Moderation.moderation_queues.pop(guild_id, None)
        if guild_id in Moderation.moderations_launched:
            Moderation.moderations_launched.remove(guild_id)

    async def start_moderations_service(self, guild_id, alert_channel_id=None):
        """Set guild moderation and start the service"""
        self.set_guild_moderated(guild_id)
        try:
            moderations_channel = await self.check_and_launch_moderations(
                guild_id,
                int(alert_channel_id or Moderation.moderation_alerts_channel or 0),
            )
            if moderations_channel is None:
                raise ValueError("Moderation was stopped during startup")
        except BaseException:
            self.set_guild_moderated(guild_id, False)
            raise
        self.set_moderated_alert_channel(guild_id, moderations_channel.id)

    async def restart_moderations_service(self, ctx):
        """restarts the moderation of the guild it's run in"""
        if not self.check_guild_moderated(ctx.guild_id):
            await ctx.respond(
                "Moderations are not enabled, can't restart",
                ephemeral=True,
                delete_after=30,
            )
            return

        await ctx.respond(
            "The moderations service is being restarted...",
            ephemeral=True,
            delete_after=30,
        )
        await self.stop_moderations_service(ctx.guild_id)
        await ctx.send_followup(
            "The moderations service was stopped..", ephemeral=True, delete_after=30
        )
        await self.start_moderations_service(
            ctx.guild_id, self.get_moderated_alert_channel(ctx.guild_id)
        )
        await ctx.send_followup(
            "The moderations service was restarted successfully.",
            ephemeral=True,
            delete_after=30,
        )

    async def build_moderation_settings_embed(self, category, mod_set):
        embed = discord.Embed(
            title="Moderation Settings",
            description="The moderation settings for this guild for the type: "
            + category,
            color=discord.Color.yellow() if category == "warn" else discord.Color.red(),
        )

        # Add each key_value pair in the mod_set to the embed, make them fairly small
        for key, value in mod_set.items():
            embed.add_field(name=key, value=value, inline=False)

        return embed

    async def config_command(
        self,
        ctx: discord.ApplicationContext,
        config_type: str,
        hate,
        hate_threatening,
        self_harm,
        sexual,
        sexual_minors,
        violence,
        violence_graphic,
    ):
        """command handler for assigning threshold values for warn or delete"""
        all_args = [
            hate,
            hate_threatening,
            self_harm,
            sexual,
            sexual_minors,
            violence,
            violence_graphic,
        ]
        await ctx.defer(ephemeral=True)

        # Case for printing the current config
        if all(value is None for value in all_args) and config_type != "reset":
            await ctx.respond(
                ephemeral=True,
                embed=await self.build_moderation_settings_embed(
                    config_type,
                    (
                        self.get_or_set_warn_set(ctx.guild_id)
                        if config_type == "warn"
                        else self.get_or_set_delete_set(ctx.guild_id)
                    ),
                ),
            )
            return

        if any(value is not None and not 0 <= value <= 1 for value in all_args):
            await ctx.respond("Thresholds must be between 0 and 1.", ephemeral=True)
            return

        if config_type == "reset":
            self.set_delete_set(ctx.guild_id, self.default_delete_set)
            self.set_warn_set(ctx.guild_id, self.default_warn_set)
        elif config_type in {"warn", "delete"}:
            current = (
                self.get_or_set_warn_set(ctx.guild_id)
                if config_type == "warn"
                else self.get_or_set_delete_set(ctx.guild_id)
            )
            thresholds = ThresholdSet(
                *(
                    old if value is None else value
                    for value, old in zip(all_args, current.values())
                )
            )
            setter = self.set_warn_set if config_type == "warn" else self.set_delete_set
            setter(ctx.guild_id, thresholds)
        else:
            await ctx.respond("Choose warn, delete, or reset.", ephemeral=True)
            return

        if self.check_guild_moderated(ctx.guild_id):
            await self.restart_moderations_service(ctx)
        else:
            await ctx.respond("Moderation thresholds saved.", ephemeral=True)

    async def moderations_test_command(
        self, ctx: discord.ApplicationContext, prompt: str
    ):
        """command handler for checking moderation values of a given input"""
        await ctx.defer()
        response = await self.model.send_moderations_request(prompt)
        await ctx.respond(response["results"][0]["category_scores"])
        await ctx.send_followup(response["results"][0]["flagged"])
