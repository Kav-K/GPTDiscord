import random
import re
from bs4 import BeautifulSoup
import aiohttp

from services.environment_service import EnvService
from services.usage_service import UsageService


class Search:
    def __init__(self, gpt_model, pinecone_service):
        self.model = gpt_model
        self.pinecone_service = pinecone_service
        self.google_search_api_key = EnvService.get_google_search_api_key()
        self.google_search_engine_id = EnvService.get_google_search_engine_id()

    async def get_links(self, query):
        """Search the web for a query"""
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://www.googleapis.com/customsearch/v1?key={self.google_search_api_key}&cx={self.google_search_engine_id}&q={query}"
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    # Return a list of the top 5 links
                    return [item["link"] for item in data["items"][:5]]
                else:
                    return "An error occurred while searching."

    async def search(self, query):
        # Get the links for the query
        links = await self.get_links(query)

        # For each link, crawl the page and get all the text that's not HTML garbage.
        # Concatenate all the text for a given website into one string and save it into an array:
        texts = []
        for link in links:
            async with aiohttp.ClientSession() as session:
                async with session.get(link, timeout=5) as response:
                    if response.status == 200:
                        soup = BeautifulSoup(await response.read(), "html.parser")
                        # Find all the content between <p> tags and join them together and then append to texts
                        texts.append(" ".join([p.text for p in soup.find_all("p")]))
                    else:
                        pass
        print("Finished retrieving text content from the links")

        # For each text in texts, split it up into 500 character chunks and create embeddings for it
        # The pinecone service uses conversation_id, but we can use it here too to keep track of the "search", each
        # conversation_id represents a unique search.
        conversation_id = random.randint(0, 100000000)
        for text in texts:
            # Split the text into 150 character chunks without using re
            chunks = [text[i : i + 500] for i in range(0, len(text), 500)]
            # Create embeddings for each chunk
            for chunk in chunks:
                # Create an embedding for the chunk
                embedding = await self.model.send_embedding_request(chunk)
                # Upsert the embedding for the conversation ID
                self.pinecone_service.upsert_conversation_embedding(
                    self.model, conversation_id, chunk, 0
                )
        print("Finished creating embeddings for the text")

        # Now that we have all the embeddings for the search, we can embed the query and then
        # query pinecone for the top 5 results
        query_embedding = await self.model.send_embedding_request(query)
        results = self.pinecone_service.get_n_similar(
            conversation_id, query_embedding, n=3
        )
        # Get only the first elements of each result
        results = [result[0] for result in results]

        # Construct a query for GPT3 to use these results to answer the query
        GPT_QUERY = f"This is a search query. I want to know the answer to the query: {query}. Here are some results from the web: {[str(result) for result in results]}. \n\n Answer:"
        # Generate the answer
        # Use the tokenizer to determine token amount of the query
        await self.model.send_request(
            GPT_QUERY, UsageService.count_tokens_static(GPT_QUERY)
        )

        print(texts)
