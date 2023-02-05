import asyncio
import os
import random
import re
import traceback
from functools import partial

from bs4 import BeautifulSoup
import aiohttp
from gpt_index import (
    QuestionAnswerPrompt,
    GPTSimpleVectorIndex,
    BeautifulSoupWebReader,
    Document, PromptHelper, LLMPredictor, OpenAIEmbedding,
)
from gpt_index.readers.web import DEFAULT_WEBSITE_EXTRACTOR
from langchain import OpenAI

from services.environment_service import EnvService
from services.usage_service import UsageService


class Search:
    def __init__(self, gpt_model, usage_service):
        self.model = gpt_model
        self.usage_service = usage_service
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

    async def search(self, query, user_api_key, search_scope, nodes):
        DEFAULT_SEARCH_NODES = 2
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
            # First, attempt a connection with a timeout of 3 seconds to the link, if the timeout occurs, don't
            # continue to the document loading.
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(link, timeout=3) as response:
                        pass  # Only catch timeout errors, allow for redirects for now..
            except:
                traceback.print_exc()
                continue

            try:
                document = await self.loop.run_in_executor(
                    None, partial(self.index_webpage, link)
                )
                [documents.append(doc) for doc in document]
            except Exception as e:
                traceback.print_exc()

        prompthelper = PromptHelper(4096, 1024, 20)

        index = GPTSimpleVectorIndex(documents)

        llm_predictor = LLMPredictor(llm=OpenAI(model_name="text-davinci-003"))
        embedding_model = OpenAIEmbedding()
        # Now we can search the index for a query:
        response = index.query(query,embed_model=embedding_model,llm_predictor=llm_predictor,prompt_helper=prompthelper, similarity_top_k=nodes or DEFAULT_SEARCH_NODES, text_qa_template=self.qaprompt)
        await self.usage_service.update_usage(llm_predictor.last_token_usage, embedding_model.last_token_usage)

        return response
