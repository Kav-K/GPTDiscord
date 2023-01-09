import asyncio
import os
import traceback
from datetime import datetime
from pathlib import Path

import discord

from models.openai_model import Model
from models.usage_service_model import UsageService

usage_service = UsageService(Path(os.environ.get("DATA_DIR", os.getcwd())))
model = Model(usage_service)


class Moderation:
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
    def build_admin_moderated_message(moderated_message, response_message):

        # Create a discord embed to send to the user when their message gets moderated
        embed = discord.Embed(
            title="A message was moderated in the "
            + moderated_message.message.guild.name
            + " server",
            description=f"Message from {moderated_message.message.author.mention} was moderated: {moderated_message.message.content}",
            colour=discord.Colour.yellow(),
        )
        # Get the link to the moderated message
        link = f"https://discord.com/channels/{response_message.guild.id}/{response_message.channel.id}/{response_message.id}"
        # set the link of the embed
        embed.add_field(name="Moderated message link", value=link, inline=False)
        return embed

    @staticmethod
    def determine_moderation_result(text, response):
        HATE_THRESHOLD = 0.005
        HATE_VIOLENCE_THRESHOLD = 0.05
        SELF_HARM_THRESHOLD = 0.05
        SEXUAL_THRESHOLD = 0.91
        SEXUAL_MINORS_THRESHOLD = 0.1
        VIOLENCE_THRESHOLD = 0.08
        VIOLENCE_GRAPHIC_THRESHOLD = 0.1

        extreme_hatred_qualifiers = [
            "i fucking hate",
            "fucking hate",
            "i fucking despise",
        ]

        thresholds = [
            HATE_THRESHOLD,
            HATE_VIOLENCE_THRESHOLD,
            SELF_HARM_THRESHOLD,
            SEXUAL_THRESHOLD,
            SEXUAL_MINORS_THRESHOLD,
            VIOLENCE_THRESHOLD,
            VIOLENCE_GRAPHIC_THRESHOLD,
        ]
        threshold_iterator = [
            "hate",
            "hate/threatening",
            "self-harm",
            "sexual",
            "sexual/minors",
            "violence",
            "violence/graphic",
        ]

        category_scores = response["results"][0]["category_scores"]

        flagged = response["results"][0]["flagged"]

        # Iterate the category scores using the threshold_iterator and compare the values to thresholds
        for category, threshold in zip(threshold_iterator, thresholds):
            if category == "hate":
                if (
                    "hate" in text.lower()
                ):  # The word "hate" makes the model oversensitive. This is a (bad) workaround.
                    threshold = 0.1
                if any(word in text.lower() for word in extreme_hatred_qualifiers):
                    threshold = 0.6

            if category_scores[category] > threshold:
                return True

        return False

    # This function will be called by the bot to process the message queue
    @staticmethod
    async def process_moderation_queue(
        moderation_queue, PROCESS_WAIT_TIME, EMPTY_WAIT_TIME, moderations_alert_channel
    ):
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
                        to_moderate.message.content, response
                    )

                    if moderation_result:
                        # Take care of the flagged message
                        response_message = await to_moderate.message.reply(
                            embed=Moderation.build_moderation_embed()
                        )
                        # Do the same response as above but use an ephemeral message
                        await to_moderate.message.delete()

                        # Send to the moderation alert channel
                        if moderations_alert_channel:
                            await moderations_alert_channel.send(
                                embed=Moderation.build_admin_moderated_message(
                                    to_moderate, response_message
                                )
                            )

                else:
                    await moderation_queue.put(to_moderate)
                # Sleep for a short time before processing the next message
                # This will prevent the bot from spamming messages too quickly
                await asyncio.sleep(PROCESS_WAIT_TIME)
            except:
                traceback.print_exc()
                pass
