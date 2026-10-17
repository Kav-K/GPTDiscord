"""Real embedded Qdrant storage; only the paid embedding provider is simulated."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

pytest.importorskip("qdrant_client")
from qdrant_client import QdrantClient, models

from services.qdrant_service import QdrantService

THREAD = 1560736974896234498
MODEL = "text-embedding-3-small"


def vector(first=1.0, second=0.0):
    return [first, second] + [0.0] * (QdrantService.DIMENSIONS - 2)


@pytest.fixture
def memory(tmp_path):
    service = QdrantService.from_config(path=tmp_path / "qdrant", embedding_model=MODEL)
    try:
        yield service
    finally:
        service.close()


def embedder(values=None):
    return SimpleNamespace(
        send_embedding_request=AsyncMock(return_value=values or vector())
    )


@pytest.mark.asyncio
async def test_persistent_chunks_round_trip_with_stable_ids_and_personal_key(tmp_path):
    path = tmp_path / "qdrant"
    service = QdrantService.from_config(path=path, embedding_model=MODEL)
    text = "α" * 1000 + "β" * 7
    stamp = 2**53 + 1
    model = embedder()
    try:
        result = await service.upsert_conversation_embedding(
            model, THREAD, text, stamp, custom_api_key="personal-test-key"
        )
        assert result == vector()
        assert [
            call.args[0] for call in model.send_embedding_request.await_args_list
        ] == ["α" * 500, "α" * 500, "β" * 7]
        assert all(
            call.kwargs["custom_api_key"] == "personal-test-key"
            for call in model.send_embedding_request.await_args_list
        )
        before = service.get_all_for_conversation(THREAD)["matches"]
        assert len(before) == 3
        assert {item["metadata"]["conversation_id"] for item in before} == {str(THREAD)}
        assert {item["metadata"]["timestamp"] for item in before} == {stamp}
        assert all(str(UUID(item["id"])) == item["id"] for item in before)
        await service.upsert_conversation_embedding(
            model, THREAD, text, stamp, custom_api_key="personal-test-key"
        )
        assert service.client.count(service.collection_name, exact=True).count == 3
        assert service.get_all_for_conversation(THREAD)["matches"] == before
    finally:
        service.close()

    restored = QdrantService.from_config(path=path, embedding_model=MODEL)
    try:
        assert "".join(restored.get_all_conversation_items(THREAD)) == text
        assert restored.get_all_for_conversation(THREAD)["matches"] == before
        assert restored.get_n_similar(THREAD, vector(), n=3) == [
            ("α" * 500, stamp),
            ("α" * 500, stamp),
            ("β" * 7, stamp),
        ]
    finally:
        restored.close()


@pytest.mark.asyncio
async def test_similarity_selects_relevant_turns_then_returns_chronological_order(
    memory,
):
    await memory.upsert_conversation_embedding(
        embedder(vector(0.0, 1.0)), THREAD, "Different topic", 30
    )
    await memory.upsert_conversation_embedding(
        embedder(vector(0.8)), THREAD, "Relevant later turn", 20
    )
    await memory.upsert_conversation_embedding(
        embedder(vector()), THREAD, "Relevant earlier turn", 10
    )
    assert memory.get_n_similar(THREAD, vector(), n=2) == [
        ("Relevant earlier turn", 10),
        ("Relevant later turn", 20),
    ]
    assert memory.get_n_similar(THREAD, vector(), n=1) == [
        ("Relevant earlier turn", 10)
    ]
    assert memory.get_n_similar(THREAD, vector(), n=0) == []


@pytest.mark.asyncio
async def test_adjacent_snowflakes_models_and_unscoped_legacy_points_are_isolated(
    memory,
):
    assert float(THREAD) == float(THREAD + 1)
    other_model = QdrantService(
        memory.client, memory.collection_name, "text-embedding-3-large"
    )
    await memory.upsert_conversation_embedding(
        embedder(), THREAD, "Small model thread", 1
    )
    await memory.upsert_conversation_embedding(
        embedder(), THREAD + 1, "Neighbor thread", 1
    )
    await other_model.upsert_conversation_embedding(
        embedder(), THREAD, "Large model thread", 1
    )
    memory.client.upsert(
        collection_name=memory.collection_name,
        points=[
            models.PointStruct(
                id=str(uuid4()),
                vector=vector(),
                payload={
                    "conversation_id": THREAD,
                    "embedding_model": MODEL,
                    "text": "Legacy numeric ID",
                },
            ),
            models.PointStruct(
                id=str(uuid4()),
                vector=vector(),
                payload={
                    "conversation_id": str(THREAD),
                    "text": "Unscoped legacy point",
                },
            ),
            models.PointStruct(
                id=str(uuid4()),
                vector=vector(),
                payload={"conversation_id": str(THREAD), "embedding_model": MODEL},
            ),
            models.PointStruct(
                id=str(uuid4()),
                vector=vector(),
                payload={
                    "conversation_id": str(THREAD),
                    "embedding_model": MODEL,
                    "text": "Malformed legacy point",
                    "timestamp": "yesterday",
                    "chunk_index": 0,
                },
            ),
        ],
        wait=True,
    )
    memory.upsert_basic("Unassociated text", vector())
    for service, thread, expected in (
        (memory, THREAD, "Small model thread"),
        (memory, THREAD + 1, "Neighbor thread"),
        (other_model, THREAD, "Large model thread"),
    ):
        assert service.get_n_similar(thread, vector()) == [(expected, 1)]
        assert service.get_all_conversation_items(thread) == [expected]
        assert [
            item["metadata"]["text"]
            for item in service.get_all_for_conversation(thread)["matches"]
        ] == [expected]
    assert memory.get_all_conversation_items(THREAD + 2) == []


@pytest.mark.asyncio
async def test_same_text_in_different_model_namespaces_does_not_overwrite(memory):
    other = QdrantService(
        memory.client, memory.collection_name, "text-embedding-ada-002"
    )
    await memory.upsert_conversation_embedding(embedder(), THREAD, "Identical text", 1)
    await other.upsert_conversation_embedding(embedder(), THREAD, "Identical text", 1)
    assert memory.client.count(memory.collection_name, exact=True).count == 2
    assert (
        memory.get_all_conversation_items(THREAD)
        == other.get_all_conversation_items(THREAD)
        == ["Identical text"]
    )
    assert (
        memory.get_all_for_conversation(THREAD)["matches"][0]["id"]
        != other.get_all_for_conversation(THREAD)["matches"][0]["id"]
    )


@pytest.mark.asyncio
async def test_scroll_pagination_keeps_all_turns_in_order(memory, monkeypatch):
    for stamp in (5, 1, 3, 2, 4):
        await memory.upsert_conversation_embedding(
            embedder(), THREAD, f"Turn {stamp}", stamp
        )
    actual_scroll = memory.client.scroll
    pages = []

    def small_page(**kwargs):
        kwargs["limit"] = 2
        result = actual_scroll(**kwargs)
        pages.append(len(result[0]))
        return result

    monkeypatch.setattr(memory.client, "scroll", small_page)
    assert memory.get_all_conversation_items(THREAD) == [
        f"Turn {stamp}" for stamp in range(1, 6)
    ]
    assert pages == [2, 2, 1]


@pytest.mark.asyncio
async def test_concurrent_thread_writes_and_worker_reads_persist(memory):
    await asyncio.gather(
        *[
            memory.upsert_conversation_embedding(
                embedder(), THREAD + number, f"Thread {number}", number
            )
            for number in range(12)
        ]
    )
    results = await asyncio.gather(
        *[
            asyncio.to_thread(memory.get_n_similar, THREAD + number, vector())
            for number in range(12)
        ]
    )
    assert results == [[(f"Thread {number}", number)] for number in range(12)]
    assert memory.client.count(memory.collection_name, exact=True).count == 12


@pytest.mark.parametrize(
    "configuration",
    [
        models.VectorParams(size=8, distance=models.Distance.DOT),
        models.VectorParams(size=1536, distance=models.Distance.COSINE),
        {"named": models.VectorParams(size=1536, distance=models.Distance.DOT)},
    ],
)
def test_incompatible_collection_is_refused_without_recreating_it(configuration):
    client = QdrantClient(location=":memory:")
    client.create_collection(collection_name="existing", vectors_config=configuration)
    try:
        stored_vector = (
            {"named": vector()}
            if isinstance(configuration, dict)
            else [0.0] * configuration.size
        )
        identifier = str(uuid4())
        client.upsert(
            collection_name="existing",
            points=[
                models.PointStruct(
                    id=identifier,
                    vector=stored_vector,
                    payload={"unrelated": "Existing data"},
                )
            ],
            wait=True,
        )
        before = client.get_collection("existing")
        with pytest.raises(ValueError, match="1536-dimensional DOT"):
            QdrantService(client, "existing", MODEL)
        assert (
            client.get_collection("existing").config.params.vectors
            == before.config.params.vectors
        )
        assert client.retrieve("existing", [identifier])[0].payload == {
            "unrelated": "Existing data"
        }
    finally:
        client.close()


@pytest.mark.asyncio
async def test_wrong_embedding_dimensions_fail_before_any_write(memory):
    with pytest.raises(ValueError, match="1536-dimensional"):
        await memory.upsert_conversation_embedding(
            embedder([0.1, 0.2]), THREAD, "A turn", 1
        )
    with pytest.raises(ValueError, match="1536-dimensional"):
        memory.get_n_similar(THREAD, [0.1, 0.2])
    assert memory.client.count(memory.collection_name, exact=True).count == 0


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"url": "http://localhost:6333", "path": "/unused"},
        {"path": "/unused", "api_key": "unused-test-key"},
    ],
)
def test_ambiguous_or_unused_connection_settings_are_refused(config):
    with pytest.raises(ValueError, match="QDRANT_"):
        QdrantService.from_config(**config)


def test_failed_collection_validation_releases_persistent_path_lock(tmp_path):
    client = QdrantClient(path=str(tmp_path))
    client.create_collection(
        collection_name="conversation-embeddings",
        vectors_config=models.VectorParams(size=8, distance=models.Distance.DOT),
    )
    client.close()
    with pytest.raises(ValueError, match="1536-dimensional DOT"):
        QdrantService.from_config(path=tmp_path, embedding_model=MODEL)
    reopened = QdrantClient(path=str(tmp_path))
    try:
        assert (
            reopened.get_collection(
                "conversation-embeddings"
            ).config.params.vectors.size
            == 8
        )
    finally:
        reopened.close()
