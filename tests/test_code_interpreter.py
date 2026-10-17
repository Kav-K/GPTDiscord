"""Offline coverage for the current E2B SDK's sandbox contract."""

import base64
import asyncio
import importlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("e2b_code_interpreter")
pytest.importorskip("langchain_openai")
pytest.importorskip("langchain_classic")
pytest.importorskip("langchain_community")

from e2b_code_interpreter.models import Execution, ExecutionError, Logs, Result


class FakeFiles:
    def __init__(self):
        self.contents = {}
        self.directories = set()

    def make_dir(self, path):
        self.directories.add(path)

    def write(self, path, data):
        self.contents[path] = data
        return SimpleNamespace(path=path, name=path.rsplit("/", 1)[-1])

    def read(self, path, format="text", request_timeout=None):
        assert format == "bytes"
        assert request_timeout == 30
        return bytearray(self.contents[path])

    def list(self, path, depth=1):
        assert depth == 10
        return [
            SimpleNamespace(path=filename, type=SimpleNamespace(value="file"))
            for filename in self.contents
            if filename.startswith(path + "/")
        ] + [SimpleNamespace(path=path + "/nested", type=SimpleNamespace(value="dir"))]


class FakeSandbox:
    def __init__(self):
        self.files = FakeFiles()
        self.command_calls = []
        self.code_calls = []
        self.commands = SimpleNamespace(run=self.run_command)
        self.execution = Execution()
        self.killed = False

    def run_code(self, code, timeout=None):
        self.code_calls.append((code, timeout))
        return self.execution

    def run_command(self, cmd, timeout=None, cwd=None, user=None):
        self.command_calls.append((cmd, timeout, cwd, user))
        return SimpleNamespace(stdout="command output", stderr="warning", exit_code=0)

    def kill(self):
        self.killed = True


@pytest.fixture
def sandbox_module(monkeypatch, tmp_path):
    monkeypatch.setenv("SHARE_DIR", str(tmp_path))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    module = importlib.import_module("cogs.code_interpreter_service_cog")
    monkeypatch.setattr(module, "USER_INPUT_API_KEYS", False)
    sandbox = FakeSandbox()
    create_calls = []

    def create(*, api_key, timeout):
        create_calls.append((api_key, timeout))
        return sandbox

    monkeypatch.setattr(module, "Sandbox", SimpleNamespace(create=create))
    monkeypatch.setattr(module.EnvService, "get_e2b_api_key", lambda: "offline-e2b-key")
    return module, sandbox, create_calls


def test_execution_preserves_logs_errors_and_downloadable_results(sandbox_module):
    module, sandbox, create_calls = sandbox_module
    executor = module.CodeInterpreterService.SessionedCodeExecutor()
    sandbox.files.write("/home/user/artifacts/nested/report, final.csv", b"a,b\n1,2")
    sandbox.execution = Execution(
        logs=Logs(stdout=["hello\n"], stderr=["warning\n"]),
        results=[Result(text="42", png=base64.b64encode(b"image bytes").decode())],
        error=ExecutionError("ValueError", "bad input", "traceback text"),
    )

    output = executor.execute_code_sync("print('hello'); 42")

    assert create_calls == [("offline-e2b-key", 3600)]
    assert executor.is_sessioned()
    assert sandbox.code_calls == [("print('hello'); 42", 30)]
    assert "hello\n\n42" in output
    assert "warning\n\nValueError: bad input\ntraceback text" in output
    artifacts = executor.get_artifact_names()
    assert len(artifacts) == 2
    assert "/home/user/artifacts/nested/report, final.csv" in artifacts
    image_path = next(path for path in artifacts if path.endswith(".png"))
    assert executor.download_file(image_path) == b"image bytes"


@pytest.mark.asyncio
async def test_upload_and_async_execution_use_current_files_api(sandbox_module):
    module, sandbox, _ = sandbox_module
    executor = module.CodeInterpreterService.SessionedCodeExecutor()

    path = await executor.upload_file_async("../../input.csv", b"data")
    assert path == "/home/user/input.csv"
    assert executor.download_file(path) == b"data"
    assert "NO__ARTIFACTS" in await executor.execute_code_async("1 + 1")
    assert "command output" in await executor.run_command_async("pwd")
    executor.close()
    assert sandbox.killed
    assert not executor.is_sessioned()


def test_package_tools_install_into_the_expected_environment(sandbox_module):
    module, sandbox, _ = sandbox_module
    executor = module.CodeInterpreterService.SessionedCodeExecutor()

    executor.install_python_package("numpy>=2")
    executor.install_system_package("htop")

    assert sandbox.command_calls == [
        ("python -m pip install -- 'numpy>=2'", 120, "/home/user", None),
        ("apt-get update && apt-get install -y -- htop", 120, None, "root"),
    ]


def test_failed_sandbox_initialization_is_reported_without_live_calls(
    sandbox_module, monkeypatch
):
    module, sandbox, _ = sandbox_module

    def fail(path):
        raise RuntimeError("unavailable sandbox")

    monkeypatch.setattr(sandbox.files, "make_dir", fail)
    executor = module.CodeInterpreterService.SessionedCodeExecutor()
    assert not executor.is_sessioned()
    assert sandbox.killed


@pytest.mark.asyncio
async def test_command_wires_system_and_python_tools_separately(
    sandbox_module, monkeypatch
):
    module, sandbox, _ = sandbox_module
    captured = {}

    def create_agent(llm, tools, system_message):
        captured.update(tools={tool.name: tool for tool in tools})
        return object()

    monkeypatch.setattr(module, "create_chat_llm", lambda **kwargs: object())
    monkeypatch.setattr(module, "create_chat_agent", create_agent)
    monkeypatch.setattr(module, "safe_ctx_respond", AsyncMock())
    monkeypatch.setattr(module, "GOOGLE_API_KEY", None)
    thread = SimpleNamespace(id=123, send=AsyncMock(), edit=AsyncMock())
    message = SimpleNamespace(create_thread=AsyncMock(return_value=thread))
    ctx = SimpleNamespace(
        user=SimpleNamespace(id=456, name="Dev"),
        defer=AsyncMock(),
        send=AsyncMock(return_value=message),
    )
    cog = module.CodeInterpreterService(None, None, None, None, None)

    await cog.code_interpreter_chat_command(ctx, "gpt-6.1-sol", 0, 1)
    captured["tools"]["Install-system-package-tool"].invoke("htop")
    captured["tools"]["Install-python-package-tool"].invoke("numpy")

    assert sandbox.command_calls[0][0].startswith("apt-get")
    assert sandbox.command_calls[1][0].startswith("python -m pip")
    assert thread.id in cog.chat_agents
    assert cog.thread_owners[thread.id] == 456


@pytest.mark.asyncio
async def test_listener_ignores_non_session_messages_before_moderation(
    sandbox_module, monkeypatch
):
    module, _, _ = sandbox_module
    moderate = AsyncMock()
    monkeypatch.setitem(
        sys.modules,
        "services.moderations_service",
        SimpleNamespace(
            Moderation=SimpleNamespace(simple_moderate_and_respond=moderate),
        ),
    )
    monkeypatch.setattr(module, "PRE_MODERATE", True)
    bot_user = object()
    cog = module.CodeInterpreterService(
        SimpleNamespace(user=bot_user), None, None, None, None
    )
    message = SimpleNamespace(
        author=object(),
        guild=object(),
        channel=SimpleNamespace(id=123),
        type=module.discord.MessageType.default,
        content="An unrelated server message",
        reply=AsyncMock(),
        delete=AsyncMock(),
    )
    cog.thread_awaiting_responses.append(123)

    await cog.on_message(message)

    moderate.assert_not_awaited()
    message.reply.assert_not_awaited()
    message.delete.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("personal_key", ["personal-offline-key", None])
async def test_code_chat_requires_and_passes_personal_api_key(
    sandbox_module, monkeypatch, personal_key
):
    module, _, create_calls = sandbox_module
    monkeypatch.setattr(module, "USER_INPUT_API_KEYS", True)
    key_db = object()
    monkeypatch.setattr(module, "USER_KEY_DB", key_db)
    get_key = AsyncMock(return_value=personal_key)
    monkeypatch.setitem(
        sys.modules,
        "services.text_service",
        SimpleNamespace(
            TextService=SimpleNamespace(get_user_api_key=get_key),
        ),
    )
    llm_calls = []

    def create_llm(**kwargs):
        llm_calls.append(kwargs)
        return object()

    monkeypatch.setattr(module, "create_chat_llm", create_llm)
    monkeypatch.setattr(module, "create_chat_agent", lambda *args: object())
    monkeypatch.setattr(module, "safe_ctx_respond", AsyncMock())
    monkeypatch.setattr(module, "GOOGLE_API_KEY", None)
    thread = SimpleNamespace(id=123)
    message = SimpleNamespace(create_thread=AsyncMock(return_value=thread))
    ctx = SimpleNamespace(
        user=SimpleNamespace(id=456, name="Dev"),
        defer=AsyncMock(),
        send=AsyncMock(return_value=message),
    )
    cog = module.CodeInterpreterService(None, None, None, None, None)

    await cog.code_interpreter_chat_command(ctx, "gpt-6.1-sol", 0, 1)

    get_key.assert_awaited_once_with(456, ctx, key_db)
    if personal_key:
        assert llm_calls == [
            {
                "model": "gpt-6.1-sol",
                "temperature": 0,
                "top_p": 1,
                "api_key": personal_key,
            }
        ]
        assert create_calls
    else:
        assert not llm_calls
        assert not create_calls
        ctx.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_personal_key_session_rejects_other_participants_before_tools(
    sandbox_module, monkeypatch
):
    module, _, _ = sandbox_module
    monkeypatch.setattr(module, "USER_INPUT_API_KEYS", True)
    monkeypatch.setattr(module, "PRE_MODERATE", True)
    moderate = AsyncMock()
    monkeypatch.setitem(
        sys.modules,
        "services.moderations_service",
        SimpleNamespace(
            Moderation=SimpleNamespace(simple_moderate_and_respond=moderate),
        ),
    )
    cog = module.CodeInterpreterService(
        SimpleNamespace(user=SimpleNamespace(id=999)), None, None, None, None
    )
    agent = SimpleNamespace(invoke=Mock())
    session = SimpleNamespace(
        upload_file_async=AsyncMock(), execute_code_sync=Mock(), close=Mock()
    )
    cog.chat_agents[123] = agent
    cog.sessions[123] = session
    cog.thread_owners[123] = 456
    attachment = SimpleNamespace(filename="input.csv", read=AsyncMock())
    message = SimpleNamespace(
        author=SimpleNamespace(id=789),
        guild=object(),
        channel=SimpleNamespace(id=123),
        type=module.discord.MessageType.default,
        content="Run code using this file",
        attachments=[attachment],
        reply=AsyncMock(),
        delete=AsyncMock(),
    )

    await cog.on_message(message)

    message.reply.assert_awaited_once()
    assert "creator's API key" in message.reply.await_args.args[0]
    agent.invoke.assert_not_called()
    session.upload_file_async.assert_not_awaited()
    session.execute_code_sync.assert_not_called()
    session.close.assert_not_called()
    attachment.read.assert_not_awaited()
    moderate.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_reserves_session_before_another_message_can_execute(
    sandbox_module, monkeypatch
):
    module, _, _ = sandbox_module
    monkeypatch.setattr(module, "message_has_access", AsyncMock(return_value=True))
    monkeypatch.setattr(module, "PRE_MODERATE", False)
    entered, release = asyncio.Event(), asyncio.Event()

    async def upload(*args):
        entered.set()
        await release.wait()
        return "/home/user/input.csv"

    session = SimpleNamespace(
        upload_file_async=AsyncMock(side_effect=upload), get_artifact_names=lambda: []
    )
    agent = SimpleNamespace(invoke=Mock(return_value={"output": "Complete"}))
    converser = SimpleNamespace(deletion_queue=asyncio.Queue())
    cog = module.CodeInterpreterService(
        None,
        None,
        SimpleNamespace(update_usage_memory=lambda *args: None),
        None,
        converser,
    )
    cog.bot = SimpleNamespace(user=SimpleNamespace(id=999))
    cog.sessions[123], cog.chat_agents[123] = session, agent

    def message(*, attachment=False):
        return SimpleNamespace(
            author=SimpleNamespace(id=456, bot=False),
            guild=SimpleNamespace(name="Test"),
            channel=SimpleNamespace(id=123, trigger_typing=AsyncMock()),
            type=module.discord.MessageType.reply,
            content="Execute this",
            attachments=[SimpleNamespace(filename="input.csv")] if attachment else [],
            reply=AsyncMock(return_value=SimpleNamespace(delete=AsyncMock())),
        )

    monkeypatch.setattr(
        module, "read_attachment_bytes", AsyncMock(return_value=b"data")
    )
    first, second = message(attachment=True), message()
    task = asyncio.create_task(cog.on_message(first))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await cog.on_message(second)
        agent.invoke.assert_not_called()
        assert "wait" in second.reply.await_args.args[0].lower()
    finally:
        release.set()
        await asyncio.wait_for(task, 1)
    agent.invoke.assert_called_once()
    assert cog.thread_awaiting_responses == []


@pytest.mark.asyncio
async def test_failed_discord_reply_releases_code_session(sandbox_module, monkeypatch):
    module, _, _ = sandbox_module
    monkeypatch.setattr(module, "message_has_access", AsyncMock(return_value=True))
    monkeypatch.setattr(module, "PRE_MODERATE", False)
    cog = module.CodeInterpreterService(
        SimpleNamespace(user=SimpleNamespace(id=999)),
        None,
        SimpleNamespace(update_usage_memory=lambda *args: None),
        None,
        None,
    )
    cog.sessions[123] = SimpleNamespace(get_artifact_names=lambda: [])
    cog.chat_agents[123] = SimpleNamespace(invoke=lambda *args: {"output": "Answer"})
    message = SimpleNamespace(
        author=SimpleNamespace(id=456, bot=False),
        guild=SimpleNamespace(name="Test"),
        channel=SimpleNamespace(id=123, trigger_typing=AsyncMock()),
        type=module.discord.MessageType.default,
        content="Execute this",
        attachments=[],
        reply=AsyncMock(side_effect=RuntimeError("Discord unavailable")),
    )
    with pytest.raises(RuntimeError, match="Discord unavailable"):
        await cog.on_message(message)
    assert cog.thread_awaiting_responses == []


@pytest.mark.asyncio
async def test_personal_key_artifacts_cannot_be_downloaded_by_other_participants(
    sandbox_module, monkeypatch
):
    module, _, _ = sandbox_module
    monkeypatch.setattr(module, "USER_INPUT_API_KEYS", True)
    cog = SimpleNamespace(thread_owners={123: 456})
    session = SimpleNamespace(download_file=Mock(return_value=b"private artifact"))
    ctx = SimpleNamespace(channel=SimpleNamespace(id=123, send=AsyncMock()))
    button = module.DownloadButton(
        ctx, cog, session, ["/home/user/artifacts/result.txt"]
    )
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=789), response=SimpleNamespace(send_message=AsyncMock())
    )

    await button.callback(interaction)

    session.download_file.assert_not_called()
    ctx.channel.send.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
