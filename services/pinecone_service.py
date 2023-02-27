import pinecone


class PineconeService:
    def __init__(self, index: pinecone.Index):
        self.index = index

    def upsert_basic(self, text, embeddings):
        self.index.upsert([(text, embeddings)])

    def get_all_for_conversation(self, conversation_id: int):
        response = self.index.query(
            top_k=100, filter={"conversation_id": conversation_id}
        )
        return response

    async def upsert_conversation_embedding(
        self, model, conversation_id: int, text, timestamp, custom_api_key=None
    ):
        # If the text is > 512 characters, we need to split it up into multiple entries.
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
                self.index.upsert(
                    [(chunk, embedding)],
                    metadata={
                        "conversation_id": conversation_id,
                        "timestamp": timestamp,
                    },
                )
            return first_embedding
        embedding = await model.send_embedding_request(
            text, custom_api_key=custom_api_key
        )
        self.index.upsert(
            [
                (
                    text,
                    embedding,
                    {"conversation_id": conversation_id, "timestamp": timestamp},
                )
            ]
        )
        return embedding

    def get_n_similar(self, conversation_id: int, embedding, n=10):
        response = self.index.query(
            vector=embedding,
            top_k=n,
            include_metadata=True,
            filter={"conversation_id": conversation_id},
        )
        # print(response)
        relevant_phrases = [
            (match["id"], match["metadata"]["timestamp"])
            for match in response["matches"]
        ]
        # Sort the relevant phrases based on the timestamp
        relevant_phrases.sort(key=lambda x: x[1])
        return relevant_phrases

    def get_all_conversation_items(self, conversation_id: int):
        response = self.index.query(
            vector=[0] * 1536,
            top_k=1000, filter={"conversation_id": conversation_id}
        )
        phrases = [match["id"] for match in response["matches"]]

        # Sort on timestamp
        phrases.sort(key=lambda x: x[1])
        return phrases
