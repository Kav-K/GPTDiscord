from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams
from qdrant_client.http import models
import uuid

class QdrantService:
    def __init__(self, client: QdrantClient):
        self.client = client
    
    def make_collection(self):
        self.client.create_collection(
            collection_name="conversation-embeddings",
            vectors_config=VectorParams(size=1536, distance=Distance.COSINE))
    
    def upsert_basic(self, text, embedding, conversation_id: int, timestamp):
        self.client.upsert(
            collection_name = "conversation-embeddings",
            points = [
                models.PointStruct(
                    # Can't upsert the text as the ID like pinecone, use a random UUID instead and add text as a payload
                    id= str(uuid.uuid4()),
                    payload={
                        "text" : text,
                        "conversation_id" : conversation_id,
                        "timestamp" : timestamp,
                    },
                    vector= embedding,
                )
            ]
        )

    async def upsert_conversation_embedding(self, model, conversation_id:int, text, timestamp, custom_api_key=None):
        first_embedding = None
        if len(text) > 500:
            # Split the text into 512 character chunks
            chunks = [text[i : i + 500] for i in range(0, len(text), 500)]
            for chunk in chunks:
                # Create an embedding for the split chunk
                embedding = await model.send_embedding_request(
                    chunk, custom_api_key=custom_api_key
                )
                if not first_embedding:
                    first_embedding = embedding
                self.upsert_basic(chunk, embedding, conversation_id, timestamp)
            return first_embedding
        embedding = await model.send_embedding_request(
            text, custom_api_key=custom_api_key
        )
        self.upsert_basic(text, embedding, conversation_id, timestamp)
        return embedding
    
    def get_n_similar(self, conversation_id: int, embedding, n=10):
        response = self.client.search(
            collection_name= "conversation-embeddings",
            query_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="conversation_id",
                        match = models.MatchValue(
                            value=conversation_id,
                        ),
                    )
                ]
            ),
            query_vector=embedding,
            with_payload=["text", "timestamp"],
            limit = n,
        )
        relevant_phrases = [
            (match.payload["text"], match.payload["timestamp"])
            for match in response
        ]
        return relevant_phrases

