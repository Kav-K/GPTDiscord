"""Conversation memory contract checks without installing or contacting Pinecone."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from services.pinecone_service import PineconeService


@pytest.mark.asyncio
async def test_long_messages_use_sdk_metadata_and_model_namespaces():
    index = Mock()
    service = PineconeService(index, embedding_model="text-embedding-3-small")
    model = SimpleNamespace(send_embedding_request=AsyncMock(return_value=[0.1] * 1536))
    text = "x" * 1500
    result = await service.upsert_conversation_embedding(
        model, 12, text, 100, custom_api_key="user-key"
    )
    assert len(result) == 1536
    assert index.upsert.call_count == 3
    records = []
    for call in index.upsert.call_args_list:
        assert call.kwargs["namespace"] == "text-embedding-3-small"
        record = call.kwargs["vectors"][0]
        assert len(record["id"]) == 64
        assert record["metadata"]["conversation_id"] == 12
        assert record["metadata"]["timestamp"] == 100
        records.append(record)
    assert "".join(record["metadata"]["text"] for record in records) == text
    assert len({record["id"] for record in records}) == 3
    model.send_embedding_request.assert_any_await("x" * 500, custom_api_key="user-key")


def test_query_sorts_text_and_reads_legacy_record_ids():
    index = Mock()
    index.query.return_value = {
        "matches": [
            {"id": "legacy text", "metadata": {"timestamp": 1}},
            {"id": "hashed-id", "metadata": {"text": "new text", "timestamp": 2}},
        ]
    }
    service = PineconeService(index, embedding_model="text-embedding-ada-002")
    assert service.get_n_similar(1, [0.0] * 1536) == [
        ("legacy text", 1),
        ("new text", 2),
    ]
    assert index.query.call_args.kwargs["namespace"] == ""
    assert service.get_all_conversation_items(1) == ["legacy text", "new text"]
    assert index.query.call_args.kwargs["include_metadata"] is True
    service.get_all_for_conversation(1)
    assert len(index.query.call_args.kwargs["vector"]) == 1536
