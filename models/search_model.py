import asyncio
import os

import tempfile
import traceback
from datetime import datetime, date
from functools import partial
from pathlib import Path

import discord
import aiohttp
import openai
import tiktoken
from langchain.chat_models import ChatOpenAI
from llama_index import (
    QuestionAnswerPrompt,
    GPTVectorStoreIndex,
    BeautifulSoupWebReader,
    Document,
    LLMPredictor,
    OpenAIEmbedding,
    SimpleDirectoryReader,
    MockEmbedding,
    ServiceContext,
    get_response_synthesizer,
)
from llama_index.callbacks import CallbackManager, TokenCountingHandler
from llama_index.composability import QASummaryQueryEngineBuilder
from llama_index.retrievers import VectorIndexRetriever
from llama_index.query_engine import RetrieverQueryEngine, MultiStepQueryEngine
from llama_index.indices.query.query_transform import StepDecomposeQueryTransform
from llama_index.prompts.chat_prompts import CHAT_REFINE_PROMPT
from llama_index.readers.web import DEFAULT_WEBSITE_EXTRACTOR
from langchain.llms import OpenAI

from models.openai_model import Models
from services.environment_service import EnvService

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
            "answer the question, say that you were unable to answer the question if there is not sufficient context to formulate a decisive answer. If the prior knowledge/context was sufficient, simply repeat it. The search query was: {query_str}\n"
        )
        self.openai_key = os.getenv("OPENAI_TOKEN")
        self.EMBED_CUTOFF = 2000

    def add_search_index(self, index, user_id, query):
        # Create a folder called "indexes/{USER_ID}" if it doesn't exist already
        Path(f"{EnvService.save_path()}/indexes/{user_id}_search").mkdir(
            parents=True, exist_ok=True
        )
        # Save the index to file under the user id
        file = f"{date.today().month}_{date.today().day}_{query[:20]}"

        index.storage_context.persist(
            persist_dir=EnvService.save_path()
            / "indexes"
            / f"{str(user_id)}_search"
            / f"{file}"
        )

    def build_search_started_embed(self):
        embed = discord.Embed(
            title="Searching the web...",
            description="Refining google search query...",
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(url="https://i.imgur.com/txHhNzL.png")
        return embed

    def build_search_refined_embed(self, refined_query):
        embed = discord.Embed(
            title="Searching the web...",
            description="Refined query:\n"
            + f"`{refined_query}`"
            + "\nRetrieving links from google...",
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(url="https://i.imgur.com/txHhNzL.png")
        return embed

    def build_search_links_retrieved_embed(self, refined_query):
        embed = discord.Embed(
            title="Searching the web...",
            description="Refined query:\n" + f"`{refined_query}`"
            "\nRetrieving webpages...",
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(url="https://i.imgur.com/txHhNzL.png")

        return embed

    def build_search_determining_price_embed(self, refined_query):
        embed = discord.Embed(
            title="Searching the web...",
            description="Refined query:\n" + f"`{refined_query}`"
            "\nPre-determining index price...",
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(url="https://i.imgur.com/txHhNzL.png")

        return embed

    def build_search_webpages_retrieved_embed(self, refined_query):
        embed = discord.Embed(
            title="Searching the web...",
            description="Refined query:\n" + f"`{refined_query}`" "\nIndexing...",
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(url="https://i.imgur.com/txHhNzL.png")

        return embed

    def build_search_indexed_embed(self, refined_query):
        embed = discord.Embed(
            title="Searching the web...",
            description="Refined query:\n" + f"`{refined_query}`"
            "\nThinking about your question...",
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(url="https://i.imgur.com/txHhNzL.png")

        return embed

    def build_search_final_embed(self, refined_query, price):
        embed = discord.Embed(
            title="Searching the web...",
            description="Refined query:\n" + f"`{refined_query}`"
            "\nDone!\n||The total price was $" + price + "||",
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(url="https://i.imgur.com/txHhNzL.png")

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
                    raise ValueError(
                        "Error while retrieving links, the response returned "
                        + str(response.status)
                        + " with the message "
                        + str(await response.text())
                    )

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
        model,
        multistep=False,
        redo=None,
    ):
        DEFAULT_SEARCH_NODES = 4
        if not user_api_key:
            os.environ["OPENAI_API_KEY"] = self.openai_key
        else:
            os.environ["OPENAI_API_KEY"] = user_api_key
        openai.api_key = os.environ["OPENAI_API_KEY"]

        # Initialize the search cost
        price = 0

        if ctx:
            in_progress_message = (
                await ctx.respond(embed=self.build_search_started_embed())
                if not redo
                else await ctx.channel.send(embed=self.build_search_started_embed())
            )

        try:
            llm_predictor_presearch = ChatOpenAI(
                max_tokens=100,
                temperature=0,
                presence_penalty=0.65,
                model_name=model,
            )

            # Refine a query to send to google custom search API
            prompt = f"You are to be given a search query for google. Change the query such that putting it into the Google Custom Search API will return the most relevant websites to assist in answering the original query. If the original query is inferring knowledge about the current day, insert the current day into the refined prompt. If the original query is inferring knowledge about the current month, insert the current month and year into the refined prompt. If the original query is inferring knowledge about the current year, insert the current year into the refined prompt. Generally, if the original query is inferring knowledge about something that happened recently, insert the current month into the refined query. Avoid inserting a day, month, or year for queries that purely ask about facts and about things that don't have much time-relevance. The current date is {str(datetime.now().date())}. Do not insert the current date if not neccessary. Respond with only the refined query for the original query. Don’t use punctuation or quotation marks.\n\nExamples:\n---\nOriginal Query: ‘Who is Harald Baldr?’\nRefined Query: ‘Harald Baldr biography’\n---\nOriginal Query: ‘What happened today with the Ohio train derailment?’\nRefined Query: ‘Ohio train derailment details {str(datetime.now().date())}’\n---\nOriginal Query: ‘Is copper in drinking water bad for you?’\nRefined Query: ‘copper in drinking water adverse effects’\n---\nOriginal Query: What's the current time in Mississauga?\nRefined Query: current time Mississauga\nNow, refine the user input query.\nOriginal Query: {query}\nRefined Query:"
            query_refined = await llm_predictor_presearch.apredict(
                text=prompt,
            )
            query_refined_text = query_refined

            print("The query refined text is: " + query_refined_text)

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

        if "vision" in model:
            llm_predictor = LLMPredictor(
                llm=ChatOpenAI(temperature=0, model=model, max_tokens=4096)
            )
        else:
            llm_predictor = LLMPredictor(llm=ChatOpenAI(temperature=0, model=model))

        token_counter = TokenCountingHandler(
            tokenizer=tiktoken.encoding_for_model(model).encode, verbose=False
        )

        callback_manager = CallbackManager([token_counter])

        service_context = ServiceContext.from_defaults(
            llm_predictor=llm_predictor,
            embed_model=embedding_model,
            callback_manager=callback_manager,
        )

        # Check price
        token_counter_mock = TokenCountingHandler(
            tokenizer=tiktoken.encoding_for_model(model).encode, verbose=False
        )
        callback_manager_mock = CallbackManager([token_counter_mock])
        embed_model_mock = MockEmbedding(embed_dim=1536)
        service_context_mock = ServiceContext.from_defaults(
            embed_model=embed_model_mock, callback_manager=callback_manager_mock
        )
        self.loop.run_in_executor(
            None,
            partial(
                GPTVectorStoreIndex.from_documents,
                documents,
                service_context=service_context_mock,
            ),
        )
        total_usage_price = await self.usage_service.get_price(
            token_counter_mock.total_embedding_token_count, "embedding"
        )
        if total_usage_price > 1.00:
            raise ValueError(
                "Doing this search would be prohibitively expensive. Please try a narrower search scope."
            )

        if not deep:
            index = await self.loop.run_in_executor(
                None,
                partial(
                    GPTVectorStoreIndex.from_documents,
                    documents,
                    service_context=service_context,
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

        else:
            if ctx:
                await self.try_edit(
                    in_progress_message,
                    self.build_search_determining_price_embed(query_refined_text),
                )

            graph_builder = QASummaryQueryEngineBuilder(service_context=service_context)

            index = await self.loop.run_in_executor(
                None,
                partial(
                    graph_builder.build_from_documents,
                    documents,
                ),
            )

        if ctx:
            await self.try_edit(
                in_progress_message, self.build_search_indexed_embed(query_refined_text)
            )

        ########################################

        if not deep:
            step_decompose_transform = StepDecomposeQueryTransform(
                service_context.llm_predictor
            )

            retriever = VectorIndexRetriever(
                index=index,
                similarity_top_k=nodes or DEFAULT_SEARCH_NODES,
            )

            response_synthesizer = get_response_synthesizer(
                response_mode=response_mode,
                use_async=True,
                refine_template=CHAT_REFINE_PROMPT,
                text_qa_template=self.qaprompt,
                service_context=service_context,
            )

            query_engine = RetrieverQueryEngine(
                retriever=retriever, response_synthesizer=response_synthesizer
            )
            multistep_query_engine = MultiStepQueryEngine(
                query_engine=query_engine,
                query_transform=step_decompose_transform,
                index_summary="Provides information about everything you need to know about this topic, use this to answer the question.",
            )
            if multistep:
                response = await self.loop.run_in_executor(
                    None,
                    partial(multistep_query_engine.query, query),
                )
            else:
                response = await self.loop.run_in_executor(
                    None,
                    partial(query_engine.query, query),
                )
        else:
            query_configs = [
                {
                    "index_struct_type": "simple_dict",
                    "query_mode": "default",
                    "query_kwargs": {"similarity_top_k": 1},
                },
                {
                    "index_struct_type": "list",
                    "query_mode": "default",
                    "query_kwargs": {
                        "response_mode": "tree_summarize",
                        "use_async": True,
                        "verbose": True,
                    },
                },
                {
                    "index_struct_type": "tree",
                    "query_mode": "default",
                    "query_kwargs": {
                        "verbose": True,
                        "use_async": True,
                        "child_branch_factor": 2,
                    },
                },
            ]
            response = await self.loop.run_in_executor(
                None,
                partial(
                    index.query,
                    query,
                ),
            )

        await self.usage_service.update_usage(
            token_counter.total_llm_token_count,
            await self.usage_service.get_cost_name(model),
        )
        await self.usage_service.update_usage(
            token_counter.total_embedding_token_count, "embedding"
        )
        price += await self.usage_service.get_price(
            token_counter.total_llm_token_count,
            await self.usage_service.get_cost_name(model),
        ) + await self.usage_service.get_price(
            token_counter.total_embedding_token_count, "embedding"
        )

        if ctx:
            await self.try_edit(
                in_progress_message,
                self.build_search_final_embed(query_refined_text, str(round(price, 6))),
            )

        return response, query_refined_text
