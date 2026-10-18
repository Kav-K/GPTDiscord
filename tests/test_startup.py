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
    from gpt3discord import __version__

    assert __version__ in result.stdout
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


@pytest.mark.parametrize("enable_code", [False, True])
@pytest.mark.parametrize("personal_keys", [False, True])
def test_core_bot_assembles_without_optional_packages_or_network(tmp_path, enable_code, personal_keys):
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
        "USER_INPUT_API_KEYS": str(personal_keys).lower(),
        "HEALTH_SERVICE_ENABLED": "false",
    }
    for key in (
        "PINECONE_TOKEN",
        "E2B_API_KEY",
        "GOOGLE_SEARCH_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_SEARCH_ENGINE_ID",
        "GOOGLE_CSE_ID",
        "DEEPL_TOKEN",
    ):
        env.pop(key, None)
    if enable_code:
        env["E2B_API_KEY"] = "offline-e2b-key"
    if personal_keys:
        env.pop("OPENAI_API_KEY", None)
        env.pop("OPENAI_TOKEN", None)
    code = """
import asyncio
import os
import socket
import discord
from pycord.multicog import Bot
import gpt3discord

code_enabled = bool(os.environ.get("E2B_API_KEY"))
personal_keys = os.environ["USER_INPUT_API_KEYS"] == "true"
closed = []
class FakeCode(discord.Cog, name="CodeInterpreterService"):
    def __init__(self, *args):
        super().__init__()
    async def close_sessions(self):
        closed.append(True)
original_optional = gpt3discord.load_optional_service
def optional_service(module_name, *args, **kwargs):
    if module_name == "cogs.code_interpreter_service_cog":
        return FakeCode
    return original_optional(module_name, *args, **kwargs)
gpt3discord.load_optional_service = optional_service

def forbid_network(*args, **kwargs):
    raise AssertionError("Startup attempted a network request")
socket.create_connection = forbid_network
socket.socket.connect = forbid_network
socket.socket.connect_ex = forbid_network
async def fake_start(self, token, *args, **kwargs):
    assert token == "test-discord-token"
    assert self.auto_sync_commands is False
    assert self.get_cog("GPT3ComCon") is not None
    assert self.get_cog("WebSearchCog") is not None
    assert self.get_cog("IndexService") is None
    assert self.get_cog("SearchService") is None
    assert self.get_cog("TranslationService") is None
    assert (self.get_cog("CodeInterpreterService") is not None) == code_enabled
    commands = {command.name: command for command in self.pending_application_commands}
    assert {"gpt", "dalle", "system", "mod", "web", "transcribe"} <= commands.keys()
    assert {"index", "internet", "translate", "languages", "Translate"}.isdisjoint(commands)
    assert ("code" in commands) == code_enabled
    assert ("setup" in commands) == personal_keys
    assert {command.name for command in commands["gpt"].subcommands} >= {"ask", "converse"}
    assert {command.name for command in commands["dalle"].subcommands} == {"draw", "optimize"}
    assert {command.name for command in commands["transcribe"].subcommands} == {"file", "link"}
    if personal_keys:
        assert gpt3discord.EnvService.get_openai_token() is None
    print("CORE_ASSEMBLED")
Bot.start = fake_start
# Simulate the base install even when this test runs in the full-extras matrix.
original_import = gpt3discord.importlib.import_module
def base_import(name, *args, **kwargs):
    if name == "cogs.search_service_cog":
        raise ImportError("Optional search dependencies are not installed")
    return original_import(name, *args, **kwargs)
gpt3discord.importlib.import_module = base_import
asyncio.run(gpt3discord.main())
assert closed == ([True] if code_enabled else [])
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
