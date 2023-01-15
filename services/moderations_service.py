import asyncio
import os
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import discord

from models.openai_model import Model
from services.environment_service import EnvService
from services.usage_service import UsageService

usage_service = UsageService(Path(os.environ.get("DATA_DIR", os.getcwd())))
model = Model(usage_service)


class ModerationResult:
    WARN = "warn"
    DELETE = "delete"
    NONE = "none"


class ThresholdSet:
    def __init__(self, h_t, hv_t, sh_t, s_t, sm_t, v_t, vg_t):
        """A set of thresholds for the OpenAI moderation endpoint

        Args:
            h_t (float): hate
            hv_t (float): hate/violence
            sh_t (float): self-harm
            s_t (float): sexual
            sm_t (float): sexual/minors
            v_t (float): violence
            vg_t (float): violence/graphic
        """        
        self.keys = [
            "hate",
            "hate/threatening",
            "self-harm",
            "sexual",
            "sexual/minors",
            "violence",
            "violence/graphic",
        ]
        self.thresholds = [
            h_t,
            hv_t,
            sh_t,
            s_t,
            sm_t,
            v_t,
            vg_t,
        ]

    # The string representation is just the keys alongside the threshold values

    def __str__(self):
        '''"key": value format'''
        # "key": value format
        return ", ".join([f"{k}: {v}" for k, v in zip(self.keys, self.thresholds)])

    def moderate(self, text, response_message):
        category_scores = response_message["results"][0]["category_scores"]
        flagged = response_message["results"][0]["flagged"]

        for category, threshold in zip(self.keys, self.thresholds):
            threshold = float(threshold)
            if category_scores[category] > threshold:
                return (True, flagged)
        return (False, flagged)


class Moderation:
    # Moderation service data
    moderation_queues = {}
    moderation_alerts_channel = EnvService.get_moderations_alert_channel()
    moderation_enabled_guilds = []
    moderation_tasks = {}
    moderations_launched = []

    def __init__(self, message, timestamp):
        self.message = message
        self.timestamp = timestamp

    @staticmethod
    def build_moderation_embed():
        # Create a discord embed to send to the user when their message gets moderated
        embed = discord.Embed(
            title="Your message was moderated",
            description="Our automatic moderation systems detected that your message was inappropriate and has been deleted. Please review the rules.",
            colour=discord.Colour.red(),
        )
        # Set the embed thumbnail
        embed.set_thumbnail(url="https://i.imgur.com/2oL8JSp.png")
        embed.set_footer(
            text="If you think this was a mistake, please contact the server admins."
        )
        return embed

    @staticmethod
    def build_admin_warning_message(
        moderated_message, deleted_message=None, timed_out=None
    ):
        embed = discord.Embed(
            title="Potentially unwanted message in the "
            + moderated_message.guild.name
            + " server",
            description=f"**Message from {moderated_message.author.mention}:** {moderated_message.content}",
            colour=discord.Colour.yellow(),
        )
        link = f"https://discord.com/channels/{moderated_message.guild.id}/{moderated_message.channel.id}/{moderated_message.id}"
        embed.add_field(name="Message link", value=link, inline=False)
        if deleted_message:
            embed.add_field(
                name="Message deleted by: ", value=deleted_message, inline=False
            )
        if timed_out:
            embed.add_field(name="User timed out by: ", value=timed_out, inline=False)
        return embed

    @staticmethod
    def build_admin_moderated_message(
        moderated_message, response_message, user_kicked=None, timed_out=None
    ):
        direct_message_object = isinstance(moderated_message, discord.Message)
        moderated_message = (
            moderated_message if direct_message_object else moderated_message.message
        )

        # Create a discord embed to send to the user when their message gets moderated
        embed = discord.Embed(
            title="A message was moderated in the "
            + moderated_message.guild.name
            + " server",
            description=f"Message from {moderated_message.author.mention} was moderated: {moderated_message.content}",
            colour=discord.Colour.red(),
        )
        # Get the link to the moderated message
        link = f"https://discord.com/channels/{response_message.guild.id}/{response_message.channel.id}/{response_message.id}"
        # set the link of the embed
        embed.add_field(name="Moderated message link", value=link, inline=False)
        if user_kicked:
            embed.add_field(name="User kicked by", value=user_kicked, inline=False)
        if timed_out:
            embed.add_field(name="User timed out by: ", value=timed_out, inline=False)
        return embed

    @staticmethod
    def determine_moderation_result(text, response, warn_set, delete_set):
        # warn_set = ThresholdSet(0.005, 0.05, 0.05, 0.91, 0.1, 0.04, 0.1)
        # delete_set = ThresholdSet(0.26, 0.26, 0.1, 0.95, 0.03, 0.85, 0.4)

        warn_result, flagged_warn = warn_set.moderate(text, response)
        delete_result, flagged_delete = delete_set.moderate(text, response)

        if delete_result:
            return ModerationResult.DELETE
        if warn_result:
            return ModerationResult.WARN
        return ModerationResult.NONE

    # This function will be called by the bot to process the message queue
    @staticmethod
    async def process_moderation_queue(
        moderation_queue,
        PROCESS_WAIT_TIME,
        EMPTY_WAIT_TIME,
        moderations_alert_channel,
        warn_set,
        delete_set,
    ):
        print("The warn and delete sets are ", warn_set, delete_set)
        while True:
            try:
                # If the queue is empty, sleep for a short time before checking again
                if moderation_queue.empty():
                    await asyncio.sleep(EMPTY_WAIT_TIME)
                    continue

                # Get the next message from the queue
                to_moderate = await moderation_queue.get()

                # Check if the current timestamp is greater than the deletion timestamp
                if datetime.now().timestamp() > to_moderate.timestamp:
                    response = await model.send_moderations_request(
                        to_moderate.message.content
                    )
                    moderation_result = Moderation.determine_moderation_result(
                        to_moderate.message.content, response, warn_set, delete_set
                    )

                    if moderation_result == ModerationResult.DELETE:
                        # Take care of the flagged message
                        response_message = await to_moderate.message.reply(
                            embed=Moderation.build_moderation_embed()
                        )
                        # Do the same response as above but use an ephemeral message
                        await to_moderate.message.delete()

                        # Send to the moderation alert channel
                        if moderations_alert_channel:
                            response_message = await moderations_alert_channel.send(
                                embed=Moderation.build_admin_moderated_message(
                                    to_moderate, response_message
                                )
                            )
                            await response_message.edit(
                                view=ModerationAdminView(
                                    to_moderate.message,
                                    response_message,
                                    True,
                                    True,
                                    True,
                                )
                            )

                    elif moderation_result == ModerationResult.WARN:
                        response_message = await moderations_alert_channel.send(
                            embed=Moderation.build_admin_warning_message(
                                to_moderate.message
                            ),
                        )
                        # Attempt to react to the to_moderate.message with a warning icon
                        try:
                            await to_moderate.message.add_reaction("⚠️")
                        except discord.errors.Forbidden:
                            pass

                        await response_message.edit(
                            view=ModerationAdminView(
                                to_moderate.message, response_message
                            )
                        )

                else:
                    await moderation_queue.put(to_moderate)
                # Sleep for a short time before processing the next message
                # This will prevent the bot from spamming messages too quickly
                await asyncio.sleep(PROCESS_WAIT_TIME)
            except Exception:
                traceback.print_exc()


class ModerationAdminView(discord.ui.View):
    def __init__(
        self,
        message,
        moderation_message,
        nodelete=False,
        deleted_message=False,
        source_deleted=False,
    ):
        super().__init__(timeout=None)  # 1 hour interval to redo.
        component_number = 0
        self.message = message
        self.moderation_message = (moderation_message,)
        self.add_item(
            TimeoutUserButton(
                self.message,
                self.moderation_message,
                component_number,
                1,
                nodelete,
                source_deleted,
            )
        )
        component_number += 1
        self.add_item(
            TimeoutUserButton(
                self.message,
                self.moderation_message,
                component_number,
                6,
                nodelete,
                source_deleted,
            )
        )
        component_number += 1
        self.add_item(
            TimeoutUserButton(
                self.message,
                self.moderation_message,
                component_number,
                12,
                nodelete,
                source_deleted,
            )
        )
        component_number += 1
        self.add_item(
            TimeoutUserButton(
                self.message,
                self.moderation_message,
                component_number,
                24,
                nodelete,
                source_deleted,
            )
        )
        component_number += 1
        if not nodelete:
            self.add_item(
                DeleteMessageButton(
                    self.message, self.moderation_message, component_number
                )
            )
            component_number += 1
        if deleted_message:
            self.add_item(
                KickUserButton(self.message, self.moderation_message, component_number)
            )


class DeleteMessageButton(discord.ui.Button["ModerationAdminView"]):
    def __init__(self, message, moderation_message, current_num):
        super().__init__(style=discord.ButtonStyle.danger, label="Delete Message")
        self.message = message
        self.moderation_message = moderation_message
        self.current_num = current_num

    async def callback(self, interaction: discord.Interaction):

        # Get the user
        await self.message.delete()
        await interaction.response.send_message(
            "This message was deleted", ephemeral=True, delete_after=10
        )
        while isinstance(self.moderation_message, tuple):
            self.moderation_message = self.moderation_message[0]
        await self.moderation_message.edit(
            embed=Moderation.build_admin_warning_message(
                self.message, deleted_message=interaction.user.mention
            ),
            view=ModerationAdminView(
                self.message, self.moderation_message, nodelete=True
            ),
        )


class KickUserButton(discord.ui.Button["ModerationAdminView"]):
    def __init__(self, message, moderation_message, current_num):
        super().__init__(style=discord.ButtonStyle.danger, label="Kick User")
        self.message = message
        self.moderation_message = moderation_message
        self.current_num = current_num

    async def callback(self, interaction: discord.Interaction):
        # Get the user and kick the user
        try:
            await self.message.author.kick(
                reason="You broke the server rules. Please rejoin and review the rules."
            )
        except Exception:
            pass
        await interaction.response.send_message(
            "This user was attempted to be kicked", ephemeral=True, delete_after=10
        )

        while isinstance(self.moderation_message, tuple):
            self.moderation_message = self.moderation_message[0]
        await self.moderation_message.edit(
            embed=Moderation.build_admin_moderated_message(
                self.message,
                self.moderation_message,
                user_kicked=interaction.user.mention,
            ),
            view=ModerationAdminView(
                self.message,
                self.moderation_message,
                nodelete=True,
                deleted_message=False,
                source_deleted=True,
            ),
        )


class TimeoutUserButton(discord.ui.Button["ModerationAdminView"]):
    def __init__(
        self, message, moderation_message, current_num, hours, nodelete, source_deleted
    ):
        super().__init__(style=discord.ButtonStyle.danger, label=f"Timeout {hours}h")
        self.message = message
        self.moderation_message = moderation_message
        self.hours = hours
        self.nodelete = nodelete
        self.current_num = current_num
        self.source_deleted = source_deleted

    async def callback(self, interaction: discord.Interaction):
        # Get the user id
        try:
            await self.message.delete()
        except Exception:
            pass

        try:
            await self.message.author.timeout(
                until=discord.utils.utcnow() + timedelta(hours=self.hours),
                reason="Breaking the server chat rules",
            )
        except Exception:
            traceback.print_exc()

        await interaction.response.send_message(
            f"This user was timed out for {self.hours} hour(s)",
            ephemeral=True,
            delete_after=10,
        )

        while isinstance(self.moderation_message, tuple):
            self.moderation_message = self.moderation_message[0]

        if not self.source_deleted:
            await self.moderation_message.edit(
                embed=Moderation.build_admin_warning_message(
                    self.message,
                    deleted_message=interaction.user.mention,
                    timed_out=interaction.user.mention,
                ),
                view=ModerationAdminView(
                    self.message, self.moderation_message, nodelete=True
                ),
            )
        else:
            await self.moderation_message.edit(
                embed=Moderation.build_admin_moderated_message(
                    self.message,
                    self.moderation_message,
                    timed_out=interaction.user.mention,
                ),
                view=ModerationAdminView(
                    self.message,
                    self.moderation_message,
                    nodelete=True,
                    deleted_message=True,
                    source_deleted=True,
                ),
            )
