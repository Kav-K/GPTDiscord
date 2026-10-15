"""Regression checks for installed entry points and startup side effects."""

import os
from pathlib import Path
import subprocess
import sys


def test_installed_entry_point_imports_without_credentials_or_side_effects(tmp_path):
    """Installing the base bot must not require optional SDKs or start services."""
    environment = os.environ.copy()
    for name in list(environment):
        if name.startswith(("OPENAI_", "DISCORD_", "PINECONE_", "REPLICATE_", "E2B_")):
            environment.pop(name)
    environment.pop("ALLOWED_GUILDS", None)
    environment.pop("PYTHONPATH", None)
    script = """
import importlib.metadata
import socket
import sys

def reject_network(*args, **kwargs):
    raise AssertionError('Importing the bot attempted a network connection')

socket.socket.connect = reject_network
socket.create_connection = reject_network
entry = next(e for e in importlib.metadata.entry_points(group='console_scripts')
             if e.name == 'gpt3discord')
assert callable(entry.load())
assert not any(name in sys.modules for name in ('llama_index', 'pinecone', 'e2b'))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert list(Path(tmp_path).iterdir()) == []
