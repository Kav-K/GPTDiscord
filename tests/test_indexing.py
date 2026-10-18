"""Offline integration coverage for current LlamaIndex and isolated credentials."""

import importlib
import json
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("llama_index.core")
pytest.importorskip("llama_index.llms.openai")
pytest.importorskip("langchain_openai")
pytest.importorskip("langchain_classic")

from llama_index.core import Document
from llama_index.core.callbacks import CallbackManager, TokenCountingHandler
from llama_index.core.embeddings import MockEmbedding
from llama_index.core.llms import MockLLM


@pytest.fixture
def integrations(monkeypatch, tmp_path):
    monkeypatch.setenv("ALLOWED_GUILDS", "1")
    monkeypatch.setenv("SHARE_DIR", str(tmp_path))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "bot-key")
    monkeypatch.setenv("OPENAI_PROJECT", "bot-project")
    monkeypatch.setenv("OPENAI_ORGANIZATION", "bot-org")
    import llama_index.core
    import tiktoken

    monkeypatch.setattr(llama_index.core, "global_tokenizer", lambda text: text.split())
    monkeypatch.setattr(
        tiktoken,
        "get_encoding",
        lambda name: SimpleNamespace(encode=lambda text: text.split()),
    )
    return importlib.import_module("services.indexing_service")


def mock_context(integrations):
    counter = TokenCountingHandler(tokenizer=lambda text: text.split())
    callbacks = CallbackManager([counter])
    return integrations.IndexContext(
        llm=MockLLM(max_tokens=4, callback_manager=callbacks),
        embed_model=MockEmbedding(embed_dim=8, callback_manager=callbacks),
        callback_manager=callbacks,
        token_counter=counter,
    )


def test_contexts_keep_credentials_account_routing_and_reasoning_parameters_separate(
    integrations,
):
    from models.openai_model import Models

    bot = integrations.create_index_context()
    personal = integrations.create_index_context(api_key="personal-key")
    assert bot.llm.api_key == "bot-key"
    assert personal.llm.api_key == "personal-key"
    assert bot.embed_model.api_key == "bot-key"
    assert personal.embed_model.api_key == "personal-key"
    assert bot.llm._get_credential_kwargs()["project"] == "bot-project"
    assert bot.embed_model._get_credential_kwargs()["organization"] == "bot-org"
    assert personal.llm._get_credential_kwargs()["project"] == ""
    assert personal.embed_model._get_credential_kwargs()["organization"] == ""
    assert personal.llm.metadata.context_window == Models.get_max_tokens(Models.DEFAULT)
    parameters = personal.llm._get_model_kwargs()
    assert "temperature" not in parameters
    assert "top_p" not in parameters
    assert parameters["store"] is False
    assert personal.embed_model.additional_kwargs["dimensions"] == 1536

    # Build real SDK requests so environment defaults and empty header values
    # cannot silently route a personal key through the bot owner's project.
    from openai._models import FinalRequestOptions

    for sdk_client in (personal.llm._client, personal.embed_model._get_client()):
        request = sdk_client._build_request(
            FinalRequestOptions.construct(
                method="post",
                url="/responses",
                json_data={"model": Models.DEFAULT},
            )
        )
        assert "openai-project" not in request.headers
        assert "openai-organization" not in request.headers


def test_chat_agents_use_isolated_sdk_clients(integrations):
    from services.agent_service import create_chat_llm
    from models.openai_model import Models

    bot = create_chat_llm(Models.DEFAULT)
    personal = create_chat_llm(Models.DEFAULT, api_key="personal-key")
    assert bot.root_client.project == "bot-project"
    assert bot.root_client.organization == "bot-org"
    assert personal.root_client.project == ""
    assert personal.root_client.organization == ""
    assert personal.root_async_client.api_key == "personal-key"
    assert personal.use_responses_api is True
    assert "temperature" not in personal._default_params
    assert "top_p" not in personal._default_params

    from openai._models import FinalRequestOptions

    request = personal.root_client._build_request(
        FinalRequestOptions.construct(
            method="post",
            url="/responses",
            json_data={"model": Models.DEFAULT},
        )
    )
    assert "openai-project" not in request.headers
    assert "openai-organization" not in request.headers


@pytest.mark.asyncio
async def test_file_index_and_query_use_current_llama_index_apis(
    integrations, tmp_path
):
    module = importlib.import_module("models.index_model")
    context = mock_context(integrations)
    path = tmp_path / "source.txt"
    path.write_text(
        "The current release includes a modern API and better document retrieval."
    )
    handler = module.Index_handler(None, None)
    index = handler.index_file(path, context)
    storage = defaultdict(module.IndexData)
    storage[1].queryable_index = index
    response = module.get_and_query(
        1, storage, "What changed?", "compact", 2, 2, context, False
    )
    assert str(response) == "text text text text"
    assert "modern API" in response.source_nodes[0].get_content()
    assert context.token_counter.total_embedding_token_count > 0
    assert context.token_counter.total_llm_token_count > 0


def test_persistence_tracks_vector_space_and_reads_legacy_archives(
    integrations, tmp_path
):
    from llama_index.core import VectorStoreIndex

    context = mock_context(integrations)
    index = VectorStoreIndex.from_documents(
        [Document(text="Archive content")], **context.index_kwargs
    )
    index._embed_model.model_name = "text-embedding-3-small"
    integrations.persist_index(index, tmp_path)
    metadata = json.loads((tmp_path / "gptdiscord-index.json").read_text())
    assert metadata == {"embedding_model": "text-embedding-3-small"}

    api_context = integrations.create_index_context(api_key="personal-key")
    restored = integrations.context_for_stored_index(tmp_path, api_context)
    assert restored.embed_model.model_name == "text-embedding-3-small"
    (tmp_path / "gptdiscord-index.json").unlink()
    legacy = integrations.context_for_stored_index(tmp_path, api_context)
    assert legacy.embed_model.model_name == "text-embedding-ada-002"
    assert legacy.embed_model.api_key == "personal-key"
    assert "dimensions" not in legacy.embed_model.additional_kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "deep,multistep", [(False, False), (True, False), (False, True)]
)
async def test_search_builds_and_queries_real_mock_index_without_live_calls(
    integrations, monkeypatch, deep, multistep
):
    module = importlib.import_module("models.search_model")
    context = mock_context(integrations)
    import llama_index.core.settings

    class ForbiddenGlobalLLM(MockLLM):
        def predict(self, *args, **kwargs):
            raise AssertionError(
                "A query used the global account instead of its request context"
            )

    class SearchMockLLM(MockLLM):
        def predict(self, prompt, **kwargs):
            if "num_choices" in kwargs:
                return '[{"choice": 1, "reason": "Relevant source"}]'
            return super().predict(prompt, **kwargs)

    monkeypatch.setattr(
        llama_index.core.settings.Settings, "_llm", ForbiddenGlobalLLM()
    )
    context.llm = SearchMockLLM(max_tokens=4, callback_manager=context.callback_manager)
    calls = []

    def create_context(**kwargs):
        calls.append(kwargs)
        return context

    class Response:
        headers = {"Content-Type": "text/html"}

        def raise_for_status(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class Session(Response):
        def __init__(self, *args, **kwargs):
            pass

        def get(self, url):
            return Response()

    usage = SimpleNamespace(
        get_price=AsyncMock(return_value=0.01),
        update_usage=AsyncMock(),
        get_cost_name=AsyncMock(return_value="gpt-6-luna"),
    )
    monkeypatch.setattr(module, "create_index_context", create_context)
    monkeypatch.setattr(
        module,
        "create_chat_llm",
        lambda *args, **kwargs: SimpleNamespace(
            ainvoke=AsyncMock(return_value=SimpleNamespace(content="latest release"))
        ),
    )
    monkeypatch.setattr(module.aiohttp, "ClientSession", Session)
    search = module.Search(None, usage)
    monkeypatch.setattr(
        search,
        "get_links",
        AsyncMock(return_value=(["https://example.com"], ["https://example.com"])),
    )
    monkeypatch.setattr(
        search,
        "index_webpage",
        lambda url: [
            Document(text="Today's release adds a modern API.", metadata={"URL": url})
        ],
    )
    response, refined = await search.search(
        None,
        "What changed today?",
        "personal-key",
        2,
        4,
        deep,
        "compact",
        "gpt-6-luna",
        multistep=multistep,
    )
    assert calls == [{"api_key": "personal-key", "model": "gpt-6-luna"}]
    assert refined == "latest release"
    assert str(response) == "text text text text"
    assert any(
        node.metadata.get("URL") == "https://example.com"
        for node in response.source_nodes
    )
    assert usage.update_usage.await_count == 2


@pytest.mark.asyncio
async def test_index_listener_ignores_non_session_messages(integrations, monkeypatch):
    module = importlib.import_module("cogs.index_service_cog")
    monkeypatch.setattr(module, "PRE_MODERATE", True)
    moderation = AsyncMock()
    monkeypatch.setattr(module.Moderation, "simple_moderate_and_respond", moderation)
    bot = SimpleNamespace(user=SimpleNamespace(id=99))
    cog = module.IndexService(bot, None, None)
    message = SimpleNamespace(
        author=SimpleNamespace(id=1),
        type=module.discord.MessageType.default,
        guild=SimpleNamespace(id=1),
        content="hello",
        channel=SimpleNamespace(id=5),
        reply=AsyncMock(),
        delete=AsyncMock(),
    )
    await cog.on_message(message)
    moderation.assert_not_awaited()
    message.reply.assert_not_awaited()
    message.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_index_chat_requires_and_forwards_personal_key(integrations, monkeypatch):
    module = importlib.import_module("cogs.index_service_cog")
    monkeypatch.setattr(module, "USER_INPUT_API_KEYS", True)
    key_lookup = AsyncMock(return_value="personal-key")
    monkeypatch.setattr(module.TextService, "get_user_api_key", key_lookup)
    cog = module.IndexService(SimpleNamespace(), None, None)
    cog.index_handler.start_index_chat = AsyncMock()
    ctx = SimpleNamespace(user=SimpleNamespace(id=42), defer=AsyncMock())
    await cog.index_chat_command(ctx, "gpt-6-luna", 0, 1)
    ctx.defer.assert_awaited_once()
    cog.index_handler.start_index_chat.assert_awaited_once_with(
        ctx,
        "gpt-6-luna",
        0,
        1,
        user_api_key="personal-key",
        user_index=None,
    )
    cog.index_handler.start_index_chat.reset_mock()
    key_lookup.return_value = None
    await cog.index_chat_command(ctx, "gpt-6-luna", 0, 1)
    cog.index_handler.start_index_chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_personal_index_chat_rejects_other_participants(
    integrations, monkeypatch
):
    module = importlib.import_module("cogs.index_service_cog")
    monkeypatch.setattr(module, "USER_INPUT_API_KEYS", True)
    bot = SimpleNamespace(user=SimpleNamespace(id=99))
    cog = module.IndexService(bot, None, None)
    cog.index_handler.index_chat_chains[5] = SimpleNamespace(owner_id=42)
    cog.index_handler.execute_index_chat_message = AsyncMock()
    message = SimpleNamespace(
        author=SimpleNamespace(id=1),
        type=module.discord.MessageType.default,
        guild=SimpleNamespace(id=1),
        content="charge the creator",
        channel=SimpleNamespace(id=5),
        reply=AsyncMock(),
        delete=AsyncMock(),
        attachments=[object()],
    )
    await cog.on_message(message)
    cog.index_handler.execute_index_chat_message.assert_not_awaited()
    assert "creator's API key" in message.reply.await_args.args[0]
    assert not cog.thread_awaiting_responses
