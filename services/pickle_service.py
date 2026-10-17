import asyncio
import errno
import os
from pathlib import Path
import pickle
import tempfile
import traceback

from services.environment_service import EnvService


def _atomic_write(path, payload):
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "wb") as file:
            descriptor = None
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        # Directory syncing is unavailable on Windows and some filesystems.
        if hasattr(os, "O_DIRECTORY"):
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                try:
                    os.fsync(directory)
                except OSError as error:
                    if error.errno not in (errno.EINVAL, errno.ENOTSUP):
                        raise
            finally:
                os.close(directory)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _write_snapshots(directory, snapshots):
    for name, payload in snapshots.items():
        _atomic_write(directory / name, payload)


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

    async def save(self, directory):
        # Serialize together on the event loop before awaiting: queued objects
        # refer to live state, which must not mutate between these snapshots.
        snapshots = {
            f"{name}.pickle": pickle.dumps(getattr(self, name))
            for name in (
                "full_conversation_history",
                "conversation_threads",
                "conversation_thread_owners",
                "instructions",
            )
        }
        await asyncio.to_thread(_write_snapshots, Path(directory), snapshots)

    @staticmethod
    async def process_pickle_queue(pickle_queue, PROCESS_WAIT_TIME, EMPTY_WAIT_TIME):
        while True:
            try:
                if pickle_queue.empty():
                    await asyncio.sleep(EMPTY_WAIT_TIME)
                    continue

                to_pickle = await pickle_queue.get()
                await to_pickle.save(EnvService.save_path() / "pickles")
                await asyncio.sleep(PROCESS_WAIT_TIME)
            except Exception:
                traceback.print_exc()
