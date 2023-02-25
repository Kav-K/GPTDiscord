import asyncio
import os
import random
import re
import tempfile
import traceback
from datetime import datetime, date
from functools import partial
from pathlib import Path

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
    GPTTreeIndex,
    MockLLMPredictor,
    MockEmbedding,
)
from gpt_index.indices.knowledge_graph import GPTKnowledgeGraphIndex
from gpt_index.readers.web import DEFAULT_WEBSITE_EXTRACTOR
from langchain import OpenAI

from services.environment_service import EnvService, app_root_path
from services.usage_service import UsageService

MAX_SEARCH_PRICE = EnvService.get_max_search_price()


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

    def add_search_index(self, index, user_id, query):
        # Create a folder called "indexes/{USER_ID}" if it doesn't exist already
        Path(f"{app_root_path()}/indexes/{user_id}_search").mkdir(
            parents=True, exist_ok=True
        )
        # Save the index to file under the user id
        file = f"{query[:20]}_{date.today().month}_{date.today().day}"

        index.save_to_disk(
            app_root_path() / "indexes" / f"{str(user_id)}_search" / f"{file}.json"
        )

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
                    raise ValueError("Could not download PDF")
        # Get the file path of this tempfile.NamedTemporaryFile
        # Save this temp file to an actual file that we can put into something else to read it
        documents = SimpleDirectoryReader(input_files=[f.name]).load_data()
        for document in documents:
            document.extra_info = {"URL": url}

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
                    raise ValueError("Error while retrieving links")

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
        self,
        ctx: discord.ApplicationContext,
        query,
        user_api_key,
        search_scope,
        nodes,
        deep,
        response_mode,
        redo=None,
    ):
        DEFAULT_SEARCH_NODES = 1
        if not user_api_key:
            os.environ["OPENAI_API_KEY"] = self.openai_key
        else:
            os.environ["OPENAI_API_KEY"] = user_api_key

        if ctx:
            in_progress_message = (
                await ctx.respond(embed=self.build_search_started_embed())
                if not redo
                else await ctx.channel.send(embed=self.build_search_started_embed())
            )

        llm_predictor = LLMPredictor(llm=OpenAI(model_name="text-davinci-003"))
        try:
            llm_predictor_presearch = OpenAI(
                max_tokens=50,
                temperature=0.25,
                presence_penalty=0.65,
                model_name="text-davinci-003",
            )

            # Refine a query to send to google custom search API
            query_refined = llm_predictor_presearch.generate(
                prompts=[
                    f"You are to be given a search query for google. Change the query such that putting it into the Google Custom Search API will return the most relevant websites to assist in answering the original query. If the original query is inferring knowledge about the current day, insert the current day into the refined prompt. If the original query is inferring knowledge about the current month, insert the current month and year into the refined prompt. If the original query is inferring knowledge about the current year, insert the current year into the refined prompt. Generally, if the original query is inferring knowledge about something that happened recently, insert the current month into the refined query. Avoid inserting a day, month, or year for queries that purely ask about facts and about things that don't have much time-relevance. The current date is {str(datetime.now().date())}. Do not insert the current date if not neccessary. Respond with only the refined query for the original query. Don’t use punctuation or quotation marks.\n\nExamples:\n---\nOriginal Query: ‘Who is Harald Baldr?’\nRefined Query: ‘Harald Baldr biography’\n---\nOriginal Query: ‘What happened today with the Ohio train derailment?’\nRefined Query: ‘Ohio train derailment details {str(datetime.now().date())}’\n---\nOriginal Query: ‘Is copper in drinking water bad for you?’\nRefined Query: ‘copper in drinking water adverse effects’\n---\nOriginal Query: What's the current time in Mississauga?\nRefined Query: current time Mississauga\nNow, refine the user input query.\nOriginal Query: {query}\nRefined Query:"
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
                    async with session.get(link, timeout=1) as response:
                        # Add another entry to links from all_links if the link is not already in it to compensate for the failed request
                        if response.status not in [200, 203, 202, 204]:
                            for link2 in all_links:
                                if link2 not in links:
                                    links.append(link2)
                                    break
                            continue
                        # Follow redirects
                        elif response.status in [301, 302, 303, 307, 308]:
                            try:
                                links.append(response.url)
                                continue
                            except:
                                continue
                        else:
                            # Detect if the link is a PDF, if it is, we load it differently
                            if response.headers["Content-Type"] == "application/pdf":
                                pdf = True

            except:
                try:
                    # Try to add a link from all_links, this is kind of messy.
                    for link2 in all_links:
                        if link2 not in links:
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

        llm_predictor = LLMPredictor(
            llm=OpenAI(model_name="text-davinci-003", max_tokens=-1)
        )

        if not deep:
            embed_model_mock = MockEmbedding(embed_dim=1536)
            self.loop.run_in_executor(
                None,
                partial(GPTSimpleVectorIndex, documents, embed_model=embed_model_mock),
            )
            total_usage_price = await self.usage_service.get_price(
                embed_model_mock.last_token_usage, True
            )
            if total_usage_price > 1.00:
                raise ValueError(
                    "Doing this search would be prohibitively expensive. Please try a narrower search scope."
                )

            index = await self.loop.run_in_executor(
                None,
                partial(
                    GPTSimpleVectorIndex,
                    documents,
                    embed_model=embedding_model,
                    use_async=True,
                ),
            )
            # save the index to disk if not a redo
            if not redo:
                self.add_search_index(
                    index,
                    ctx.user.id
                    if isinstance(ctx, discord.ApplicationContext)
                    else ctx.author.id,
                    query,
                )

            await self.usage_service.update_usage(
                embedding_model.last_token_usage, embeddings=True
            )
        else:
            llm_predictor_deep = LLMPredictor(llm=OpenAI(model_name="text-davinci-003"))
            # Try a mock call first
            llm_predictor_mock = MockLLMPredictor(4096)
            embed_model_mock = MockEmbedding(embed_dim=1536)

            await self.loop.run_in_executor(
                None,
                partial(
                    GPTTreeIndex,
                    documents,
                    embed_model=embed_model_mock,
                    llm_predictor=llm_predictor_mock,
                ),
            )
            total_usage_price = await self.usage_service.get_price(
                llm_predictor_mock.last_token_usage
            ) + await self.usage_service.get_price(
                embed_model_mock.last_token_usage, True
            )
            if total_usage_price > MAX_SEARCH_PRICE:
                await self.try_delete(in_progress_message)
                raise ValueError(
                    "Doing this deep search would be prohibitively expensive. Please try a narrower search scope. This deep search indexing would have cost ${:.2f}.".format(
                        total_usage_price
                    )
                )

            index = await self.loop.run_in_executor(
                None,
                partial(
                    GPTTreeIndex,
                    documents,
                    embed_model=embedding_model,
                    llm_predictor=llm_predictor_deep,
                    use_async=True,
                ),
            )

            # llm_predictor_deep = LLMPredictor(
            #     llm=OpenAI(model_name="text-davinci-002", temperature=0, max_tokens=-1)
            # )
            # index = await self.loop.run_in_executor(
            #     None,
            #     partial(
            #         GPTKnowledgeGraphIndex,
            #         documents,
            #         chunk_size_limit=512,
            #         max_triplets_per_chunk=2,
            #         embed_model=embedding_model,
            #         llm_predictor=llm_predictor_deep,
            #     ),
            # )
            await self.usage_service.update_usage(
                embedding_model.last_token_usage, embeddings=True
            )
            await self.usage_service.update_usage(
                llm_predictor_deep.last_token_usage, embeddings=False
            )

        if ctx:
            await self.try_edit(
                in_progress_message, self.build_search_indexed_embed(query_refined_text)
            )

        # Now we can search the index for a query:
        embedding_model.last_token_usage = 0

        if not deep:
            response = await self.loop.run_in_executor(
                None,
                partial(
                    index.query,
                    query,
                    embed_model=embedding_model,
                    llm_predictor=llm_predictor,
                    similarity_top_k=nodes or DEFAULT_SEARCH_NODES,
                    text_qa_template=self.qaprompt,
                    use_async=True,
                    response_mode=response_mode,
                ),
            )
        else:
            # response = await self.loop.run_in_executor(
            #     None,
            #     partial(
            #         index.query,
            #         query,
            #         include_text=True,
            #         embed_model=embedding_model,
            #         llm_predictor=llm_predictor_deep,
            #         use_async=True,
            #     ),
            # )
            response = await self.loop.run_in_executor(
                None,
                partial(
                    index.query,
                    query,
                    child_branch_factor=2,
                    llm_predictor=llm_predictor,
                    embed_model=embedding_model,
                    use_async=True,
                ),
            )

        await self.usage_service.update_usage(llm_predictor.last_token_usage)
        await self.usage_service.update_usage(
            embedding_model.last_token_usage, embeddings=True
        )

        if ctx:
            await self.try_delete(in_progress_message)

        return response, query_refined_text
