import asyncio
import pickle
import traceback
from datetime import datetime

import aiofiles
import discord

from services.environment_service import EnvService


class Pickler:
    def __init__(
        self,
        full_conversation_history,
        conversation_threads,
        conversation_thread_owners,
        instructions,
    ):
        self.full_conversation_history = full_conversation_history
        self.conversation_threads = conversation_threads
        self.conversation_thread_owners = conversation_thread_owners
        self.instructions = instructions

    # This function will be called by the bot to process the message queue
    @staticmethod
    async def process_pickle_queue(pickle_queue, PROCESS_WAIT_TIME, EMPTY_WAIT_TIME):
        while True:
            try:
                # If the queue is empty, sleep for a short time before checking again
                if pickle_queue.empty():
                    await asyncio.sleep(EMPTY_WAIT_TIME)
                    continue

                # Get the next object to pickle from the queue
                to_pickle = await pickle_queue.get()

                # Pickle all the objects inside to_pickle using aiofiles
                async with aiofiles.open(
                    EnvService.save_path()
                    / "pickles"
                    / "full_conversation_history.pickle",
                    "wb",
                ) as f:
                    await f.write(pickle.dumps(to_pickle.full_conversation_history))

                async with aiofiles.open(
                    EnvService.save_path() / "pickles" / "conversation_threads.pickle",
                    "wb",
                ) as f:
                    await f.write(pickle.dumps(to_pickle.conversation_threads))

                async with aiofiles.open(
                    EnvService.save_path()
                    / "pickles"
                    / "conversation_thread_owners.pickle",
                    "wb",
                ) as f:
                    await f.write(pickle.dumps(to_pickle.conversation_thread_owners))

                async with aiofiles.open(
                    EnvService.save_path() / "pickles" / "instructions.pickle", "wb"
                ) as f:
                    await f.write(pickle.dumps(to_pickle.instructions))

                await asyncio.sleep(PROCESS_WAIT_TIME)
            except Exception:
                traceback.print_exc()
