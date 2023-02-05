import asyncio
import os
import random
import re
from functools import partial

from bs4 import BeautifulSoup
import aiohttp
from gpt_index import (
    QuestionAnswerPrompt,
    GPTSimpleVectorIndex,
    BeautifulSoupWebReader,
    Document,
)
from gpt_index.readers.web import DEFAULT_WEBSITE_EXTRACTOR

from services.environment_service import EnvService
from services.usage_service import UsageService


class Search:
    def __init__(self, gpt_model):
        self.model = gpt_model
        self.google_search_api_key = EnvService.get_google_search_api_key()
        self.google_search_engine_id = EnvService.get_google_search_engine_id()
        self.loop = asyncio.get_running_loop()
        self.qaprompt = QuestionAnswerPrompt(
            "You are formulating the response to a search query given the search prompt and the context. Context information is below. The text '<|endofstatement|>' is used to separate chat entries and make it easier for you to understand the context\n"
            "---------------------\n"
            "{context_str}"
            "\n---------------------\n"
            "Never say '<|endofstatement|>'\n"
            "Given the context information and not prior knowledge, "
            "answer the question, say that you were unable to answer the question if there is not sufficient context to formulate a decisive answer. The search query was: {query_str}\n"
        )
        self.openai_key = os.getenv("OPENAI_TOKEN")

    def index_webpage(self, url) -> list[Document]:
        documents = BeautifulSoupWebReader(
            website_extractor=DEFAULT_WEBSITE_EXTRACTOR
        ).load_data(urls=[url])
        return documents

    async def get_links(self, query, search_scope=5):
        """Search the web for a query"""
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://www.googleapis.com/customsearch/v1?key={self.google_search_api_key}&cx={self.google_search_engine_id}&q={query}"
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    # Return a list of the top 5 links
                    return [item["link"] for item in data["items"][:search_scope]]
                else:
                    return "An error occurred while searching."

    async def search(self, query, user_api_key, search_scope):
        if not user_api_key:
            os.environ["OPENAI_API_KEY"] = self.openai_key
        else:
            os.environ["OPENAI_API_KEY"] = user_api_key

        # Get the links for the query
        links = await self.get_links(query, search_scope=search_scope)

        # For each link, crawl the page and get all the text that's not HTML garbage.
        # Concatenate all the text for a given website into one string and save it into an array:
        documents = []
        for link in links:
            document = await self.loop.run_in_executor(
                None, partial(self.index_webpage, link)
            )
            [documents.append(doc) for doc in document]

        index = GPTSimpleVectorIndex(documents)

        # Now we can search the index for a query:
        response = index.query(query, text_qa_template=self.qaprompt)

        return response
