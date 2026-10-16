import os
import subprocess
import sys
from pathlib import Path

import pytest

from gpt3discord import check_process_file, load_optional_service


def test_import_does_not_start_bot_or_require_tokens(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-c", "import gpt3discord; print(gpt3discord.__version__)"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(root)},
        text=True,
        capture_output=True,
        check=True,
    )
    assert "13.0.0" in result.stdout
    assert not list(tmp_path.iterdir())


def test_first_launch_reports_missing_configuration(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(root), "HEALTH_SERVICE_ENABLED": "false"}
    env.pop("DISCORD_TOKEN", None)
    result = subprocess.run(
        [sys.executable, str(root / "gpt3discord.py")],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1
    assert "DISCORD_TOKEN" in result.stderr
    assert not (tmp_path / "bot.pid").exists()


@pytest.mark.parametrize("value", ["not-a-pid", "", "-1", "0"])
def test_invalid_pid_is_removed(tmp_path, value):
    path = tmp_path / "bot.pid"
    path.write_text(value)
    assert not check_process_file(path)
    assert not path.exists()


def test_running_pid_is_detected(tmp_path):
    path = tmp_path / "bot.pid"
    path.write_text(str(os.getpid()))
    assert check_process_file(path)
    assert path.exists()


def test_missing_extra_has_actionable_error():
    with pytest.raises(RuntimeError, match=r"gpt3discord\[index\]"):
        load_optional_service("gptdiscord_nonexistent_dependency", "Service", "index")


def test_core_bot_assembles_without_optional_packages_or_network(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "PYTHONPATH": str(root),
        "DISCORD_TOKEN": "test-discord-token",
        "OPENAI_API_KEY": "test-openai-key",
        "ALLOWED_GUILDS": "123456789012345678",
        "DATA_DIR": str(tmp_path),
        "SHARE_DIR": str(tmp_path),
        "ENABLE_INDEXING": "false",
        "USER_INPUT_API_KEYS": "false",
        "HEALTH_SERVICE_ENABLED": "false",
    }
    for key in (
        "PINECONE_TOKEN",
        "E2B_API_KEY",
        "GOOGLE_SEARCH_API_KEY",
        "DEEPL_TOKEN",
    ):
        env.pop(key, None)
    code = """
import asyncio
import socket
from pycord.multicog import Bot
import gpt3discord

def forbid_network(*args, **kwargs):
    raise AssertionError("Startup attempted a network request")
socket.create_connection = forbid_network
async def fake_start(self, token, *args, **kwargs):
    assert token == "test-discord-token"
    assert self.auto_sync_commands is False
    assert self.get_cog("GPT3ComCon") is not None
    assert self.get_cog("WebSearchCog") is not None
    assert self.get_cog("IndexService") is None
    assert self.get_cog("SearchService") is None
    assert self.get_cog("CodeInterpreterService") is None
    print("CORE_ASSEMBLED")
Bot.start = fake_start
asyncio.run(gpt3discord.main())
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CORE_ASSEMBLED" in result.stdout


@pytest.mark.parametrize("running", [True, False])
def test_windows_pid_check_never_sends_a_signal(tmp_path, monkeypatch, running):
    import gpt3discord
    from unittest.mock import Mock

    path = tmp_path / "bot.pid"
    path.write_text("12345")
    monkeypatch.setattr(gpt3discord, "system", lambda: "Windows")
    query = Mock(return_value=running)
    monkeypatch.setattr(gpt3discord, "_windows_process_running", query)
    signal_probe = Mock(
        side_effect=AssertionError("Windows PID checks must not signal a process")
    )
    monkeypatch.setattr(gpt3discord.os, "kill", signal_probe)
    assert check_process_file(path) is running
    assert path.exists() is running
    query.assert_called_once_with(12345)
    signal_probe.assert_not_called()
