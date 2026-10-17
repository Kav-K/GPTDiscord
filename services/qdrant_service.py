"""Optional Qdrant conversation memory with exact thread and model isolation."""

import asyncio
import json
import math
from threading import RLock
from uuid import NAMESPACE_URL, uuid5


class QdrantService:
    DIMENSIONS = 1536

    def __init__(
        self, client, collection_name="conversation-embeddings", embedding_model=None
    ):
        from qdrant_client import models
        from services.environment_service import EnvService

        self.client = client
        self.collection_name = collection_name
        self.namespace = embedding_model or EnvService.get_openai_embedding_model()
        self._models = models
        # The embedded client's SQLite store needs serialized access when the
        # async bot offloads reads/writes to different worker threads.
        self._lock = RLock()
        if not client.collection_exists(collection_name):
            client.create_collection(
                collection_name=collection_name,
                vectors_config=models.VectorParams(
                    size=self.DIMENSIONS, distance=models.Distance.DOT
                ),
            )
        vectors = client.get_collection(collection_name).config.params.vectors
        if (
            not isinstance(vectors, models.VectorParams)
            or vectors.size != self.DIMENSIONS
            or vectors.distance != models.Distance.DOT
        ):
            raise ValueError(
                "Qdrant memory requires an unnamed 1536-dimensional DOT collection; use a separate compatible collection."
            )

    @classmethod
    def from_config(
        cls,
        *,
        url=None,
        api_key=None,
        path=None,
        collection_name="conversation-embeddings",
        embedding_model=None,
    ):
        if bool(url) == bool(path):
            raise ValueError("Configure exactly one of QDRANT_URL or QDRANT_PATH.")
        if path and api_key:
            raise ValueError("QDRANT_API_KEY applies only to a remote QDRANT_URL.")
        try:
            from qdrant_client import QdrantClient
        except ImportError as error:
            raise RuntimeError(
                "Install gpt3discord[qdrant] to enable Qdrant memory."
            ) from error
        client = QdrantClient(
            url=url,
            api_key=api_key,
            path=str(path) if path else None,
            timeout=15,
            force_disable_check_same_thread=True,
        )
        try:
            return cls(client, collection_name, embedding_model)
        except Exception:
            client.close()
            raise

    def close(self):
        with self._lock:
            self.client.close()

    def _point(
        self, text, embedding, conversation_id=None, timestamp=None, chunk_index=None
    ):
        if len(embedding) != self.DIMENSIONS:
            raise ValueError(
                "Qdrant memory requires 1536-dimensional embedding vectors."
            )
        identity = [self.namespace, str(conversation_id), timestamp, chunk_index, text]
        payload = {"text": text, "embedding_model": self.namespace}
        if conversation_id is not None:
            payload["conversation_id"] = str(conversation_id)
        if timestamp is not None:
            payload["timestamp"] = timestamp
        if chunk_index is not None:
            payload["chunk_index"] = chunk_index
        return self._models.PointStruct(
            id=str(uuid5(NAMESPACE_URL, json.dumps(identity, ensure_ascii=False))),
            vector=embedding,
            payload=payload,
        )

    def _filter(self, conversation_id):
        return self._models.Filter(
            must=[
                self._models.FieldCondition(
                    key="conversation_id",
                    match=self._models.MatchValue(value=str(conversation_id)),
                ),
                self._models.FieldCondition(
                    key="embedding_model",
                    match=self._models.MatchValue(value=self.namespace),
                ),
            ]
        )

    @staticmethod
    def _readable(point):
        payload = point.payload or {}
        stamp = payload.get("timestamp")
        chunk = payload.get("chunk_index")
        return (
            isinstance(payload.get("text"), str)
            and type(stamp) in (int, float)
            and (not isinstance(stamp, float) or math.isfinite(stamp))
            and type(chunk) is int
            and chunk >= 0
        )

    @staticmethod
    def _order(point):
        payload = point.payload or {}
        return (
            payload.get("timestamp", 0),
            payload.get("chunk_index", 0),
            str(point.id),
        )

    def upsert_basic(self, text, embeddings):
        self._upsert(self._point(text, embeddings))

    def _upsert(self, point):
        with self._lock:
            self.client.upsert(
                collection_name=self.collection_name, points=[point], wait=True
            )

    async def upsert_conversation_embedding(
        self, model, conversation_id, text, timestamp, custom_api_key=None
    ):
        first_embedding = None
        for start in range(0, max(len(text), 1), 500):
            chunk = text[start : start + 500]
            embedding = await model.send_embedding_request(
                chunk, custom_api_key=custom_api_key
            )
            if first_embedding is None:
                first_embedding = embedding
            point = self._point(
                chunk, embedding, conversation_id, timestamp, start // 500
            )
            await asyncio.to_thread(self._upsert, point)
        return first_embedding

    def get_n_similar(self, conversation_id, embedding, n=10):
        if n <= 0:
            return []
        if len(embedding) != self.DIMENSIONS:
            raise ValueError(
                "Qdrant memory requires 1536-dimensional embedding vectors."
            )
        with self._lock:
            points = self.client.query_points(
                collection_name=self.collection_name,
                query=embedding,
                query_filter=self._filter(conversation_id),
                limit=n,
                with_payload=True,
                with_vectors=False,
            ).points
        return [
            (point.payload["text"], point.payload.get("timestamp", 0))
            for point in sorted(filter(self._readable, points), key=self._order)
        ]

    def get_all_for_conversation(self, conversation_id):
        points, offset = [], None
        with self._lock:
            while True:
                page, offset = self.client.scroll(
                    collection_name=self.collection_name,
                    scroll_filter=self._filter(conversation_id),
                    limit=1000,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                points.extend(page)
                if offset is None:
                    break
        return {
            "matches": [
                {"id": str(point.id), "metadata": point.payload}
                for point in sorted(filter(self._readable, points), key=self._order)
            ]
        }

    def get_all_conversation_items(self, conversation_id):
        return [
            match["metadata"]["text"]
            for match in self.get_all_for_conversation(conversation_id)["matches"]
        ]
