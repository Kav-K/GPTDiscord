"""Application conversations use real Qdrant storage; paid API calls are simulated."""

from unittest.mock import AsyncMock

import pytest

pytest.importorskip("qdrant_client")

from services.qdrant_service import QdrantService
from tests.test_conversation_flow import conversation  # noqa: F401


@pytest.mark.asyncio
async def test_conversation_memory_persists_and_rejoins_the_next_turn(conversation, tmp_path, monkeypatch):
    flow = conversation
    path = tmp_path / "conversation-memory"
    memory = QdrantService.from_config(path=path)
    flow.cog.pinecone_service = memory
    monkeypatch.setattr(flow.model, "send_embedding_request", AsyncMock(return_value=[1.0] + [0.0] * 1535))
    flow.answers.extend(["I will remember MARIGOLD.", "Your codeword is MARIGOLD."])
    try:
        thread_id = await flow.start()
        await flow.deliver("Remember the codeword MARIGOLD.", channel=thread_id)
        stored = memory.get_all_conversation_items(thread_id)
        assert any("Remember the codeword MARIGOLD." in text for text in stored)
        assert any("I will remember MARIGOLD." in text for text in stored)
        assert memory.get_all_conversation_items(thread_id + 1) == []
        memory.close()
        memory = QdrantService.from_config(path=path)
        flow.cog.pinecone_service = memory
        assert memory.get_all_conversation_items(thread_id) == stored
        await flow.deliver("What is my codeword?", channel=thread_id)
        request = flow.provider_calls[-1]["json"]
        assert "MARIGOLD" in str(request["messages"])
        assert "What is my codeword?" in str(request["messages"])
        assert len(flow.provider_calls) == 2
        assert any("Your codeword is MARIGOLD." in text for text in memory.get_all_conversation_items(thread_id))
        assert not flow.cog.awaiting_responses
        assert not flow.cog.awaiting_thread_responses
    finally:
        memory.close()
