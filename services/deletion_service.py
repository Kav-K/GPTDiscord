import asyncio
import traceback
from datetime import datetime

import discord


class Deletion:
    def __init__(self, message, timestamp):
        self.message = message
        self.timestamp = timestamp

    # This function will be called by the bot to process the message queue
    @staticmethod
    async def process_deletion_queue(
        deletion_queue, PROCESS_WAIT_TIME, EMPTY_WAIT_TIME
    ):
        while True:
            try:
                # If the queue is empty, sleep for a short time before checking again
                if deletion_queue.empty():
                    await asyncio.sleep(EMPTY_WAIT_TIME)
                    continue

                # Get the next message from the queue
                deletion = await deletion_queue.get()

                # Check if the current timestamp is greater than the deletion timestamp
                if datetime.now().timestamp() > deletion.timestamp:
                    # If the deletion timestamp has passed, delete the message
                    # check if deletion.message is of type discord.Message
                    if isinstance(deletion.message, discord.Message):
                        await deletion.message.delete()
                    else:
                        await deletion.message.delete_original_response()
                else:
                    await deletion_queue.put(deletion)

                # Sleep for a short time before processing the next message
                # This will prevent the bot from spamming messages too quickly
                await asyncio.sleep(PROCESS_WAIT_TIME)
            except:
                traceback.print_exc()
                pass
