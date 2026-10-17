import asyncio
from collections import defaultdict
import os
import pickle
import threading
from unittest.mock import Mock

import pytest

from models.user_model import Thread
from services import pickle_service
from services.pickle_service import Pickler

NAMES = (
    "full_conversation_history",
    "conversation_threads",
    "conversation_thread_owners",
    "instructions",
)


def snapshot():
    thread = Thread(123)
    thread.model = "gpt-6.1-sol"
    thread.history = ["fixture history"]
    return Pickler(
        defaultdict(list, {123: ["fixture history"]}),
        {123: thread},
        defaultdict(list, {456: [123]}),
        {456: "fixture instruction"},
    )


def old_files(directory):
    result = {}
    for name in NAMES:
        path = directory / f"{name}.pickle"
        result[path] = pickle.dumps({"previous": name})
        path.write_bytes(result[path])
    return result


def assert_previous_readable(directory, previous):
    assert set(directory.iterdir()) == set(previous)
    for path, content in previous.items():
        assert path.read_bytes() == content
        assert pickle.loads(path.read_bytes()) == {"previous": path.stem}


async def test_success_preserves_pickled_snapshot_types_and_private_mode(tmp_path):
    previous = old_files(tmp_path)
    state = snapshot()

    await state.save(tmp_path)

    assert set(tmp_path.iterdir()) == set(previous)
    restored = {
        name: pickle.loads((tmp_path / f"{name}.pickle").read_bytes()) for name in NAMES
    }
    assert isinstance(restored["full_conversation_history"], defaultdict)
    assert restored["full_conversation_history"][123] == ["fixture history"]
    assert isinstance(restored["conversation_threads"][123], Thread)
    assert restored["conversation_threads"][123].model == "gpt-6.1-sol"
    assert restored["conversation_threads"][123].history == ["fixture history"]
    assert restored["conversation_thread_owners"][456] == [123]
    assert restored["instructions"] == {456: "fixture instruction"}
    if os.name != "nt":
        assert all(path.stat().st_mode & 0o777 == 0o600 for path in previous)


async def test_failed_final_serialization_touches_no_existing_or_temporary_files(
    tmp_path, monkeypatch
):
    previous = old_files(tmp_path)
    state = snapshot()
    state.instructions = lambda: None
    writer = Mock()
    monkeypatch.setattr(pickle_service, "_write_snapshots", writer)

    with pytest.raises((AttributeError, pickle.PicklingError)):
        await state.save(tmp_path)

    writer.assert_not_called()
    assert_previous_readable(tmp_path, previous)


@pytest.mark.parametrize("failure", ["write", "fsync", "replace"])
async def test_interrupted_write_keeps_previous_readable_and_removes_temp(
    tmp_path, monkeypatch, failure
):
    previous = old_files(tmp_path)
    if failure == "write":
        original_fdopen = pickle_service.os.fdopen

        class InterruptedFile:
            def __init__(self, descriptor, mode):
                self.file = original_fdopen(descriptor, mode)

            def __enter__(self):
                self.file.__enter__()
                return self

            def __exit__(self, *args):
                return self.file.__exit__(*args)

            def write(self, payload):
                self.file.write(payload[:7])
                self.file.flush()
                raise OSError("simulated interrupted write")

        monkeypatch.setattr(pickle_service.os, "fdopen", InterruptedFile)
    else:

        def interrupted(*args):
            raise OSError("simulated persistence failure")

        monkeypatch.setattr(pickle_service.os, failure, interrupted)

    with pytest.raises(OSError):
        await snapshot().save(tmp_path)

    assert_previous_readable(tmp_path, previous)


async def test_serialization_runs_on_event_loop_and_only_bytes_reach_worker(
    tmp_path, monkeypatch
):
    loop_thread = threading.get_ident()
    original_dumps = pickle_service.pickle.dumps
    serialization_threads = []
    worker_threads = []

    def serialize(value):
        serialization_threads.append(threading.get_ident())
        return original_dumps(value)

    def write(directory, snapshots):
        worker_threads.append(threading.get_ident())
        assert directory == tmp_path
        assert all(isinstance(payload, bytes) for payload in snapshots.values())
        assert len(serialization_threads) == 4

    monkeypatch.setattr(pickle_service.pickle, "dumps", serialize)
    monkeypatch.setattr(pickle_service, "_write_snapshots", write)

    await snapshot().save(tmp_path)

    assert serialization_threads == [loop_thread] * 4
    assert len(worker_threads) == 1
    assert worker_threads[0] != loop_thread


async def test_later_replace_failure_keeps_each_file_readable_and_cleans_temp(
    tmp_path, monkeypatch
):
    previous = old_files(tmp_path)
    original_replace = pickle_service.os.replace
    replacements = []

    def replace(source, destination):
        assert os.path.dirname(source) == str(destination.parent)
        if os.name != "nt":
            assert os.stat(source).st_mode & 0o777 == 0o600
        replacements.append(destination)
        if len(replacements) == 2:
            raise OSError("simulated later replace failure")
        original_replace(source, destination)

    monkeypatch.setattr(pickle_service.os, "replace", replace)

    with pytest.raises(OSError):
        await snapshot().save(tmp_path)

    assert set(tmp_path.iterdir()) == set(previous)
    first = tmp_path / "full_conversation_history.pickle"
    assert pickle.loads(first.read_bytes())[123] == ["fixture history"]
    for path, content in previous.items():
        if path != first:
            assert path.read_bytes() == content
            assert pickle.loads(path.read_bytes()) == {"previous": path.stem}


async def test_queue_uses_atomic_persistence(tmp_path, monkeypatch):
    (tmp_path / "pickles").mkdir()
    monkeypatch.setattr(pickle_service.EnvService, "save_path", lambda: tmp_path)
    queue = asyncio.Queue()
    await queue.put(snapshot())
    saved = asyncio.Event()
    original_write = pickle_service._write_snapshots
    loop = asyncio.get_running_loop()

    def write(directory, snapshots):
        original_write(directory, snapshots)
        loop.call_soon_threadsafe(saved.set)

    monkeypatch.setattr(pickle_service, "_write_snapshots", write)
    consumer = asyncio.create_task(Pickler.process_pickle_queue(queue, 60, 60))
    try:
        await asyncio.wait_for(saved.wait(), 3)
    finally:
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer

    restored = pickle.loads(
        (tmp_path / "pickles" / "conversation_threads.pickle").read_bytes()
    )
    assert restored[123].history == ["fixture history"]
