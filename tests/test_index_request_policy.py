"""Real SDK retry behavior for reused LlamaIndex clients and recovery turns."""

import asyncio
import importlib
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("llama_index.core")
pytest.importorskip("llama_index.llms.openai")
pytest.importorskip("llama_index.embeddings.openai")

import httpx
from llama_index.core.llms import ChatMessage
from openai import RateLimitError
from openai._base_client import AsyncAPIClient, SyncAPIClient

from services.request_context import single_attempt_requests


@pytest.mark.parametrize("kind", ["response", "embedding"])
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_reused_index_clients_apply_single_attempt_policy_per_turn(
    monkeypatch, kind, asynchronous
):
    monkeypatch.setenv("OPENAI_API_KEY", "offline-key")
    monkeypatch.setattr(SyncAPIClient, "_sleep_for_retry", Mock())
    monkeypatch.setattr(AsyncAPIClient, "_sleep_for_retry", AsyncMock())
    module = importlib.import_module("services.indexing_service")
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(
            429,
            json={"error": {"message": "Rate limited", "type": "rate_limit_error"}},
        )

    sync_http = httpx.Client(transport=httpx.MockTransport(respond))
    async_http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    model_class = module.IndexResponses if kind == "response" else module.IndexEmbedding
    model = model_class(
        model="gpt-6-luna" if kind == "response" else "text-embedding-3-small",
        api_key="offline-key",
        http_client=sync_http,
        async_http_client=async_http,
    )
    assert model.max_retries == 0  # No duplicate LlamaIndex retry loop.

    async def invoke():
        if kind == "response":
            messages = [ChatMessage(role="user", content="Test retry policy")]
            if asynchronous:
                await model.achat(messages)
            else:
                await asyncio.to_thread(model.chat, messages)
        elif asynchronous:
            await model.aget_text_embedding("Test retry policy")
        else:
            await asyncio.to_thread(model.get_text_embedding, "Test retry policy")

    try:
        # The same long-lived model/client crosses both policy boundaries.
        for single_attempt, expected in [(True, 1), (False, 3), (True, 1)]:
            before = len(calls)
            token = single_attempt_requests.set(single_attempt)
            try:
                with pytest.raises(RateLimitError):
                    await invoke()
            finally:
                single_attempt_requests.reset(token)
            assert len(calls) - before == expected
            assert model.max_retries == 0
        assert all(
            request.url.path.endswith("responses" if kind == "response" else "embeddings")
            for request in calls
        )
        assert not single_attempt_requests.get()
    finally:
        sync_http.close()
        await async_http.aclose()
