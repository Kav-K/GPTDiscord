import asyncio
import os
import random
import re
import tempfile
import traceback
from functools import partial

import discord
from bs4 import BeautifulSoup
import aiohttp
from gpt_index import (
    QuestionAnswerPrompt,
    GPTSimpleVectorIndex,
    BeautifulSoupWebReader,
    Document,
    PromptHelper,
    LLMPredictor,
    OpenAIEmbedding,
    SimpleDirectoryReader,
)
from gpt_index.readers.web import DEFAULT_WEBSITE_EXTRACTOR
from langchain import OpenAI

from services.environment_service import EnvService, app_root_path
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
        self.EMBED_CUTOFF = 2000

    def build_search_started_embed(self):
        embed = discord.Embed(
            title="Searching the web...",
            description="Refining google search query...",
            color=0x00FF00,
        )
        return embed

    def build_search_refined_embed(self, refined_query):
        embed = discord.Embed(
            title="Searching the web...",
            description="Refined query: "
            + refined_query
            + "\n\nRetrieving links from google...",
            color=0x00FF00,
        )
        return embed

    def build_search_links_retrieved_embed(self, refined_query):
        embed = discord.Embed(
            title="Searching the web...",
            description="Refined query: "
            + refined_query
            + "\n\nRetrieved links from Google\n\n"
            "Retrieving webpages...",
            color=0x00FF00,
        )
        return embed

    def build_search_webpages_retrieved_embed(self, refined_query):
        embed = discord.Embed(
            title="Searching the web...",
            description="Refined query: "
            + refined_query
            + "\n\nRetrieved links from Google\n\n"
            "Retrieved webpages\n\n"
            "Indexing...",
            color=0x00FF00,
        )
        return embed

    def build_search_indexed_embed(self, refined_query):
        embed = discord.Embed(
            title="Searching the web...",
            description="Refined query: "
            + refined_query
            + "\n\nRetrieved links from Google\n\n"
            "Retrieved webpages\n\n"
            "Indexed\n\n"
            "Thinking about your question...",
            color=0x00FF00,
        )
        return embed

    def index_webpage(self, url) -> list[Document]:
        documents = BeautifulSoupWebReader(
            website_extractor=DEFAULT_WEBSITE_EXTRACTOR
        ).load_data(urls=[url])
        return documents

    async def index_pdf(self, url) -> list[Document]:
        # Download the PDF at the url and save it to a tempfile
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.read()
                    f = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
                    f.write(data)
                    f.close()
                else:
                    return "An error occurred while downloading the PDF."
        # Get the file path of this tempfile.NamedTemporaryFile
        # Save this temp file to an actual file that we can put into something else to read it
        documents = SimpleDirectoryReader(input_files=[f.name]).load_data()
        print("Loaded the PDF document data")

        # Delete the temporary file
        return documents

    async def get_links(self, query, search_scope=2):
        """Search the web for a query"""
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://www.googleapis.com/customsearch/v1?key={self.google_search_api_key}&cx={self.google_search_engine_id}&q={query}"
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    # Return a list of the top 2 links
                    return (
                        [item["link"] for item in data["items"][:search_scope]],
                        [item["link"] for item in data["items"]],
                    )
                else:
                    print(
                        "The Google Search API returned an error: "
                        + str(response.status)
                    )
                    return ["An error occurred while searching.", None]

    async def try_edit(self, message, embed):
        try:
            await message.edit(embed=embed)
        except Exception:
            traceback.print_exc()
            pass

    async def try_delete(self, message):
        try:
            await message.delete()
        except Exception:
            traceback.print_exc()
            pass

    async def search(
        self, ctx: discord.ApplicationContext, query, user_api_key, search_scope, nodes
    ):
        DEFAULT_SEARCH_NODES = 1
        if not user_api_key:
            os.environ["OPENAI_API_KEY"] = self.openai_key
        else:
            os.environ["OPENAI_API_KEY"] = user_api_key

        if ctx:
            in_progress_message = await ctx.respond(
                embed=self.build_search_started_embed()
            )

        llm_predictor = LLMPredictor(llm=OpenAI(model_name="text-davinci-003"))
        try:
            llm_predictor_presearch = OpenAI(
                max_tokens=30, temperature=0, model_name="text-davinci-003"
            )

            # Refine a query to send to google custom search API
            query_refined = llm_predictor_presearch.generate(
                prompts=[
                    "You are refining a query to send to the Google Custom Search API. Change the query such that putting it into the Google Custom Search API will return the most relevant websites to assist us in answering the original query. Respond with only the refined query for the original query. Don't use punctuation or quotation marks. The original query is: "
                    + query
                    + "\nRefined Query:"
                ]
            )
            query_refined_text = query_refined.generations[0][0].text
        except Exception as e:
            traceback.print_exc()
            query_refined_text = query

        if ctx:
            await self.try_edit(
                in_progress_message, self.build_search_refined_embed(query_refined_text)
            )

        # Get the links for the query
        links, all_links = await self.get_links(
            query_refined_text, search_scope=search_scope
        )

        if ctx:
            await self.try_edit(
                in_progress_message,
                self.build_search_links_retrieved_embed(query_refined_text),
            )

        if all_links is None:
            raise ValueError("The Google Search API returned an error.")

        # For each link, crawl the page and get all the text that's not HTML garbage.
        # Concatenate all the text for a given website into one string and save it into an array:
        documents = []
        for link in links:
            # First, attempt a connection with a timeout of 3 seconds to the link, if the timeout occurs, don't
            # continue to the document loading.
            pdf = False
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(link, timeout=2) as response:
                        # Add another entry to links from all_links if the link is not already in it to compensate for the failed request
                        if response.status not in [200, 203, 202, 204]:
                            for link2 in all_links:
                                if link2 not in links:
                                    print("Found a replacement link")
                                    links.append(link2)
                                    break
                            continue
                        # Follow redirects
                        elif response.status in [301, 302, 303, 307, 308]:
                            try:
                                print("Adding redirect")
                                links.append(response.url)
                                continue
                            except:
                                continue
                        else:
                            # Detect if the link is a PDF, if it is, we load it differently
                            if response.headers["Content-Type"] == "application/pdf":
                                print("Found a PDF at the link " + link)
                                pdf = True

            except:
                traceback.print_exc()
                try:
                    # Try to add a link from all_links, this is kind of messy.
                    for link2 in all_links:
                        if link2 not in links:
                            print("Found a replacement link")
                            links.append(link2)
                            break
                except:
                    pass

                continue

            try:
                if not pdf:
                    document = await self.loop.run_in_executor(
                        None, partial(self.index_webpage, link)
                    )
                else:
                    document = await self.index_pdf(link)
                [documents.append(doc) for doc in document]
            except Exception as e:
                traceback.print_exc()

        if ctx:
            await self.try_edit(
                in_progress_message,
                self.build_search_webpages_retrieved_embed(query_refined_text),
            )

        embedding_model = OpenAIEmbedding()

        index = await self.loop.run_in_executor(
            None, partial(GPTSimpleVectorIndex, documents, embed_model=embedding_model)
        )

        if ctx:
            await self.try_edit(
                in_progress_message, self.build_search_indexed_embed(query_refined_text)
            )

        await self.usage_service.update_usage(
            embedding_model.last_token_usage, embeddings=True
        )

        llm_predictor = LLMPredictor(
            llm=OpenAI(model_name="text-davinci-003", max_tokens=-1)
        )

        # Now we can search the index for a query:
        embedding_model.last_token_usage = 0

        response = await self.loop.run_in_executor(
            None,
            partial(
                index.query,
                query,
                verbose=True,
                embed_model=embedding_model,
                llm_predictor=llm_predictor,
                similarity_top_k=nodes or DEFAULT_SEARCH_NODES,
                text_qa_template=self.qaprompt,
            ),
        )

        await self.usage_service.update_usage(llm_predictor.last_token_usage)
        await self.usage_service.update_usage(
            embedding_model.last_token_usage, embeddings=True
        )

        if ctx:
            await self.try_delete(in_progress_message)

        return response
