import asyncio


class Message:
    def __init__(self, content, channel):
        self.content = content
        self.channel = channel

    # This function will be called by the bot to process the message queue
    @staticmethod
    async def process_message_queue(message_queue, PROCESS_WAIT_TIME, EMPTY_WAIT_TIME):
        while True:
            await asyncio.sleep(PROCESS_WAIT_TIME)
            # If the queue is empty, sleep for a short time before checking again
            if message_queue.empty():
                await asyncio.sleep(EMPTY_WAIT_TIME)
                continue

            # Get the next message from the queue
            message = await message_queue.get()

            # Send the message
            try:
                await message.channel.send(message.content)
            except Exception:
                pass

            # Sleep for a short time before processing the next message
            # This will prevent the bot from spamming messages too quickly
            await asyncio.sleep(PROCESS_WAIT_TIME)
