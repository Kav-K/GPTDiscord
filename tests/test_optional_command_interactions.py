"""Exercise slash-command routing against Discord's one initial response rule."""

import importlib
from types import SimpleNamespace

import discord
import pytest

pytest.importorskip("llama_index.core")
pytest.importorskip("llama_index.llms.openai")
pytest.importorskip("langchain_openai")
pytest.importorskip("e2b_code_interpreter")


class StrictResponse:
    def __init__(self, events):
        self.events = events
        self.initial = None
        self.modals = []

    def is_done(self):
        return self.initial is not None

    def consume(self, kind):
        if self.is_done():
            raise discord.InteractionResponded(self)
        self.initial = kind
        self.events.append(kind)

    async def defer(self, **kwargs):
        self.consume("defer")

    async def send_modal(self, modal):
        self.consume("modal")
        self.modals.append(modal)

    async def send_message(self, content=None, **kwargs):
        self.consume("message")


class StrictFollowup:
    def __init__(self, response, events):
        self.response = response
        self.events = events
        self.messages = []

    async def send(self, content=None, **kwargs):
        assert self.response.is_done(), "Followups require an initial response"
        self.events.append("followup")
        self.messages.append((content, kwargs))


class StrictInteraction:
    def __init__(self, events):
        self._state = None
        self.events = events
        self.response = StrictResponse(events)
        self.followup = StrictFollowup(self.response, events)
        self.user = SimpleNamespace(id=42, name="Tester")
        self.channel = SimpleNamespace(send=self.send_channel)

    async def respond(self, content=None, **kwargs):
        if self.response.is_done():
            return await self.followup.send(content, **kwargs)
        return await self.response.send_message(content, **kwargs)

    async def send_channel(self, *args, **kwargs):
        assert self.response.initial == "defer", (
            "Create chats only after acknowledging a saved key"
        )
        self.events.append("channel_send")
        return SimpleNamespace(create_thread=self.create_thread)

    async def create_thread(self, **kwargs):
        self.events.append("create_thread")
        return SimpleNamespace(id=1234)


class StrictContext(discord.ApplicationContext):
    # Keep py-cord's real user/response/modal/defer/respond/followup properties;
    # channel delivery is the only outbound operation replaced in this context.
    async def send(self, *args, **kwargs):
        return await self.channel.send(*args, **kwargs)


class KeyDB(dict):
    def __init__(self, events, saved):
        super().__init__({42: "saved-personal-key"} if saved else {})
        self.events = events

    def get(self, user_id, default=None):
        self.events.append("key_lookup")
        return super().get(user_id, default)

    def __contains__(self, user_id):
        self.events.append("key_lookup")
        return super().__contains__(user_id)


ENTRIES = [
    ("chat_code", "code", {"model": "gpt-6-luna", "temperature": 0, "top_p": 1}),
    ("chat", "search", {"model": "gpt-6-luna", "search_scope": 2}),
    ("talk", "index", {"model": "gpt-6-luna", "temperature": 0, "top_p": 1}),
    (
        "load_index",
        "index",
        {"user_index": "archive", "server_index": None, "search_index": None},
    ),
    (
        "query",
        "index",
        {
            "query": "What changed?",
            "nodes": 2,
            "response_mode": "compact",
            "child_branch_factor": 2,
            "model": "gpt-6-luna",
            "multistep": False,
        },
    ),
    ("set_file", "index", {"file": object(), "link": None}),
    ("set_recurse_link", "index", {"link": "https://example.com", "depth": 1}),
    ("set_discord", "index", {"channel": object(), "message_limit": 250}),
    ("discord_backup", "index", {"message_limit": 250}),
    ("compose", "index", {"name": "combined"}),
    (
        "search",
        "search",
        {
            "query": "What changed?",
            "scope": 2,
            "nodes": 2,
            "deep": False,
            "response_mode": "compact",
            "model": "gpt-6-luna",
            "multistep": False,
        },
    ),
]


@pytest.fixture
def modules(monkeypatch, tmp_path):
    monkeypatch.setenv("ALLOWED_GUILDS", "1")
    monkeypatch.setenv("SHARE_DIR", str(tmp_path))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "offline-google-key")
    monkeypatch.setenv("GOOGLE_SEARCH_ENGINE_ID", "offline-engine-id")
    # Real key lookup/modal construction is deliberately left in place.
    return {
        "commands": importlib.import_module("cogs.commands"),
        "code": importlib.import_module("cogs.code_interpreter_service_cog"),
        "search": importlib.import_module("cogs.search_service_cog"),
        "index": importlib.import_module("cogs.index_service_cog"),
    }


def build_cogs(modules, monkeypatch, ctx, db):
    bot = SimpleNamespace(user=SimpleNamespace(id=999))
    providers = []
    cogs = {}
    for name in ("code", "search", "index"):
        module = modules[name]
        monkeypatch.setattr(module, "USER_INPUT_API_KEYS", True)
        monkeypatch.setattr(module, "USER_KEY_DB", db)
        monkeypatch.setattr(module, "PRE_MODERATE", False)
    cogs["code"] = modules["code"].CodeInterpreterService(bot, None, None, None, None)
    cogs["search"] = modules["search"].SearchService(bot, None, None, None, None)
    cogs["index"] = modules["index"].IndexService(bot, None, None)

    def llm(**kwargs):
        assert ctx.response.initial == "defer"
        providers.append(("llm", (), kwargs))
        return object()

    def agent(*args, **kwargs):
        return SimpleNamespace(memory=SimpleNamespace())

    for name in ("code", "search"):
        monkeypatch.setattr(modules[name], "create_chat_llm", llm)
        monkeypatch.setattr(modules[name], "create_chat_agent", agent)
        monkeypatch.setattr(modules[name], "GOOGLE_API_KEY", None)
    monkeypatch.setattr(modules["search"], "WOLFRAM_API_KEY", None)
    monkeypatch.setattr(
        modules["search"],
        "GoogleSearchAPIWrapper",
        lambda **kwargs: SimpleNamespace(run=lambda query: "result"),
    )
    session = SimpleNamespace(
        is_sessioned=lambda: True,
        execute_code_sync=lambda code: "done",
        install_python_package=lambda package: "done",
        install_system_package=lambda package: "done",
        run_command_sync=lambda command: "done",
    )
    monkeypatch.setattr(cogs["code"], "SessionedCodeExecutor", lambda: session)

    def index_provider(name):
        async def call(*args, **kwargs):
            assert ctx.response.initial == "defer"
            providers.append((name, args, kwargs))

        return call

    cogs["index"].index_handler = SimpleNamespace(
        **{
            name: index_provider(name)
            for name in (
                "start_index_chat",
                "set_file_index",
                "set_link_index_recurse",
                "set_discord_index",
                "backup_discord",
                "load_index",
                "query",
                "compose",
            )
        }
    )

    async def search(*args, **kwargs):
        assert ctx.response.initial == "defer"
        providers.append(("search", args, kwargs))
        raise ValueError("Stop before any live provider call")

    cogs["search"].model = SimpleNamespace(search=search)
    commands = modules["commands"].Commands.__new__(modules["commands"].Commands)
    commands.code_interpreter_cog = cogs["code"]
    commands.search_cog = cogs["search"]
    commands.index_cog = cogs["index"]
    return commands, providers


@pytest.mark.asyncio
@pytest.mark.parametrize("saved", [False, True], ids=["needs-setup", "saved-key"])
@pytest.mark.parametrize(
    "entry, integration, kwargs", ENTRIES, ids=[entry[0] for entry in ENTRIES]
)
async def test_optional_commands_preserve_the_initial_response_for_key_setup(
    modules,
    monkeypatch,
    saved,
    entry,
    integration,
    kwargs,
):
    events = []
    interaction = StrictInteraction(events)
    ctx = StrictContext(None, interaction)
    db = KeyDB(events, saved)
    commands, providers = build_cogs(modules, monkeypatch, ctx, db)

    await getattr(modules["commands"].Commands, entry).callback(commands, ctx, **kwargs)

    assert events[0] == "key_lookup"
    if saved:
        assert interaction.response.initial == "defer"
        assert events.count("defer") == 1
        assert not interaction.response.modals
        assert len(providers) == 1
        _, args, provider_kwargs = providers[0]
        assert (
            "saved-personal-key" in args
            or "saved-personal-key" in provider_kwargs.values()
        )
        if integration in ("code", "search") and entry != "search":
            assert events.count("create_thread") == 1
    else:
        assert interaction.response.initial == "modal"
        assert len(interaction.response.modals) == 1
        assert interaction.response.modals[0].title == "API Key Setup"
        assert "defer" not in events
        assert "channel_send" not in events
        assert "create_thread" not in events
        assert not providers
