"""Conversation memory backed by an optional Pinecone serverless index."""

import asyncio
import hashlib
from typing import Any


class PineconeService:
    DIMENSIONS = 1536

    def __init__(self, index: Any, embedding_model=None):
        from services.environment_service import EnvService

        self.index = index
        model = embedding_model or EnvService.get_openai_embedding_model()
        self.namespace = "" if model == "text-embedding-ada-002" else model

    @classmethod
    def from_config(
        cls,
        api_key,
        region="us-east-1",
        cloud="aws",
        index_name="conversation-embeddings",
    ):
        # Import only when configured; basic chat does not require Pinecone.
        from pinecone import Pinecone, ServerlessSpec

        client = Pinecone(api_key=api_key)
        if index_name not in client.list_indexes().names():
            client.create_index(
                name=index_name,
                dimension=cls.DIMENSIONS,
                metric="dotproduct",
                spec=ServerlessSpec(cloud=cloud, region=region),
            )
        return cls(client.Index(index_name))

    @staticmethod
    def _record(
        text, embeddings, conversation_id=None, timestamp=None, chunk_index=None
    ):
        # IDs have a 512 character limit. Keep text in metadata so long or
        # repeated messages cannot collide across conversations.
        digest = hashlib.sha256(
            f"{conversation_id}:{timestamp}:{chunk_index}:{text}".encode("utf-8")
        ).hexdigest()
        metadata = {"text": text}
        if conversation_id is not None:
            metadata["conversation_id"] = conversation_id
        if timestamp is not None:
            metadata["timestamp"] = timestamp
        if chunk_index is not None:
            metadata["chunk_index"] = chunk_index
        return {"id": digest, "values": embeddings, "metadata": metadata}

    def upsert_basic(self, text, embeddings):
        self.index.upsert(
            vectors=[self._record(text, embeddings)], namespace=self.namespace
        )

    def get_all_for_conversation(self, conversation_id: int):
        return self.index.query(
            namespace=self.namespace,
            vector=[0.0] * self.DIMENSIONS,
            top_k=100,
            include_metadata=True,
            filter={"conversation_id": conversation_id},
        )

    async def upsert_conversation_embedding(
        self, model, conversation_id: int, text, timestamp, custom_api_key=None
    ):
        first_embedding = None
        for start in range(0, max(len(text), 1), 500):
            chunk = text[start : start + 500]
            embedding = await model.send_embedding_request(
                chunk, custom_api_key=custom_api_key
            )
            if first_embedding is None:
                first_embedding = embedding
            await asyncio.to_thread(
                self.index.upsert,
                vectors=[
                    self._record(
                        chunk, embedding, conversation_id, timestamp, start // 500
                    )
                ],
                namespace=self.namespace,
            )
        return first_embedding

    def get_n_similar(self, conversation_id: int, embedding, n=10):
        response = self.index.query(
            namespace=self.namespace,
            vector=embedding,
            top_k=n,
            include_metadata=True,
            filter={"conversation_id": conversation_id},
        )
        relevant_phrases = [
            (
                match.get("metadata", {}).get("text", match["id"]),
                match.get("metadata", {}).get("timestamp", 0),
            )
            for match in response["matches"]
        ]
        relevant_phrases.sort(key=lambda item: item[1])
        return relevant_phrases

    def get_all_conversation_items(self, conversation_id: int):
        response = self.index.query(
            namespace=self.namespace,
            vector=[0.0] * self.DIMENSIONS,
            top_k=1000,
            include_metadata=True,
            filter={"conversation_id": conversation_id},
        )
        matches = sorted(
            response["matches"],
            key=lambda match: match.get("metadata", {}).get("timestamp", 0),
        )
        # Fall back to IDs when loading records saved by older releases.
        return [match.get("metadata", {}).get("text", match["id"]) for match in matches]
