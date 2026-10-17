import asyncio
import re

import traceback
from datetime import datetime, date
from pathlib import Path

import discord
import aiohttp
from llama_index.core import (
    PromptTemplate as QuestionAnswerPrompt,
    VectorStoreIndex,
    Document,
    get_response_synthesizer,
)
from llama_index.core.composability import QASummaryQueryEngineBuilder
from llama_index.core.retrievers import VectorIndexRetriever
from llama_index.core.query_engine import RetrieverQueryEngine, MultiStepQueryEngine
from llama_index.core.indices.query.query_transform import StepDecomposeQueryTransform
from llama_index.core.prompts.chat_prompts import CHAT_REFINE_PROMPT

from services.indexing_service import (
    create_index_context,
    read_web_documents,
    read_pdf_documents,
    persist_index,
)
from services.agent_service import create_chat_llm, agent_output_text

from services.environment_service import EnvService

MAX_SEARCH_PRICE = EnvService.get_max_search_price()


class SearchError(ValueError):
    """Actionable search failures that are safe to show in Discord."""


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
        self.openai_key = EnvService.get_openai_token()
        self.EMBED_CUTOFF = 2000

    def add_search_index(self, index, user_id, query):
        # Create a folder called "indexes/{USER_ID}" if it doesn't exist already
        Path(f"{EnvService.save_path()}/indexes/{user_id}_search").mkdir(
            parents=True, exist_ok=True
        )
        # Save the index to file under the user id
        name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", query[:20]).strip(" .")
        file = f"{date.today().month}_{date.today().day}_{name or 'search'}"

        persist_index(
            index,
            persist_dir=EnvService.save_path()
            / "indexes"
            / f"{str(user_id)}_search"
            / f"{file}",
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
            description="Refined query:\n" + f"`{refined_query}`\nIndexing...",
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
        return read_web_documents(url)

    async def index_pdf(self, url) -> list[Document]:
        return await read_pdf_documents(url)

    async def get_links(self, query, search_scope=2):
        """Search Google with properly encoded query parameters."""
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30)
        ) as session:
            links = []
            # Google returns at most ten results per page, while /internet
            # search permits up to sixteen source pages.
            for start in range(1, min(max(search_scope, 1), 16) + 1, 10):
                async with session.get(
                    "https://www.googleapis.com/customsearch/v1",
                    params={
                        "key": self.google_search_api_key,
                        "cx": self.google_search_engine_id,
                        "q": query,
                        "start": start,
                    },
                    proxy=EnvService.get_http_proxy("https://www.googleapis.com/customsearch/v1"),
                ) as response:
                    if response.status != 200:
                        raise SearchError(f"Google Search returned HTTP {response.status}.")
                    data = await response.json()
                links.extend(item["link"] for item in data.get("items", []))
                if not data.get("queries", {}).get("nextPage"):
                    break
        return links[:search_scope], links

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
        key = user_api_key or self.openai_key
        context = create_index_context(api_key=key, model=model)
        in_progress_message = None
        if ctx:
            in_progress_message = (
                await ctx.respond(embed=self.build_search_started_embed())
                if not redo
                else await ctx.channel.send(embed=self.build_search_started_embed())
            )

        refined_query = query
        try:
            llm = create_chat_llm(model, api_key=key, max_tokens=256)
            prompt = (
                "Rewrite the question as a concise Google search query. Include the current date "
                "only if the question asks about recent events. Return only the search query. "
                f"Today is {datetime.now().date()}. Question: {query}"
            )
            result = await llm.ainvoke(prompt)
            refined_query = agent_output_text(result.content).strip() or query
        except Exception:
            traceback.print_exc()

        if ctx:
            await self.try_edit(
                in_progress_message, self.build_search_refined_embed(refined_query)
            )
        links, all_links = await self.get_links(
            refined_query, search_scope=search_scope
        )
        if not all_links:
            raise SearchError("Google returned no search results for this question.")
        if ctx:
            await self.try_edit(
                in_progress_message,
                self.build_search_links_retrieved_embed(refined_query),
            )

        # Failed pages are replaced by later search results, with a bounded list.
        documents = []
        loaded_pages = 0
        for link in dict.fromkeys(links + all_links):
            if loaded_pages >= search_scope:
                break
            try:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as session:
                    async with session.get(link) as response:
                        response.raise_for_status()
                        is_pdf = "application/pdf" in response.headers.get(
                            "Content-Type", ""
                        )
                page_documents = (
                    await self.index_pdf(link)
                    if is_pdf
                    else await asyncio.to_thread(self.index_webpage, link)
                )
                documents.extend(page_documents)
                loaded_pages += 1
            except Exception:
                traceback.print_exc()
        if not documents:
            raise SearchError(
                "None of the search result pages could be read. Try a different query."
            )
        if ctx:
            await self.try_edit(
                in_progress_message,
                self.build_search_determining_price_embed(refined_query),
            )

        # Estimate the embedding charge before issuing any embedding requests.
        embedding_tokens = sum(
            len(context.token_counter.tokenizer(document.get_content()))
            for document in documents
        )
        estimated_price = await self.usage_service.get_price(
            embedding_tokens, EnvService.get_openai_embedding_model()
        )
        if estimated_price > MAX_SEARCH_PRICE:
            raise SearchError(
                "This search exceeds MAX_SEARCH_PRICE. Try a narrower search scope."
            )
        if ctx:
            await self.try_edit(
                in_progress_message,
                self.build_search_webpages_retrieved_embed(refined_query),
            )

        if deep:
            builder = QASummaryQueryEngineBuilder(
                llm=context.llm,
                embed_model=context.embed_model,
                callback_manager=context.callback_manager,
            )
            query_engine = await asyncio.to_thread(
                builder.build_from_documents, documents
            )
        else:
            index = await asyncio.to_thread(
                VectorStoreIndex.from_documents,
                documents,
                **context.index_kwargs,
            )
            if ctx and not redo:
                user = (
                    ctx.user
                    if isinstance(ctx, discord.ApplicationContext)
                    else ctx.author
                )
                self.add_search_index(index, user.id, query)
            retriever = VectorIndexRetriever(
                index=index,
                similarity_top_k=nodes or 4,
                embed_model=context.embed_model,
            )
            synthesizer = get_response_synthesizer(
                response_mode=response_mode,
                use_async=False,
                refine_template=CHAT_REFINE_PROMPT,
                text_qa_template=self.qaprompt,
                **context.query_kwargs,
            )
            query_engine = RetrieverQueryEngine(
                retriever=retriever, response_synthesizer=synthesizer
            )
            if multistep:
                query_engine = MultiStepQueryEngine(
                    query_engine=query_engine,
                    query_transform=StepDecomposeQueryTransform(llm=context.llm),
                    response_synthesizer=synthesizer,
                    index_summary="Provides information from the search result pages.",
                )
        if ctx:
            await self.try_edit(
                in_progress_message, self.build_search_indexed_embed(refined_query)
            )
        response = await asyncio.to_thread(query_engine.query, query)

        counter = context.token_counter
        cost_name = await self.usage_service.get_cost_name(model)
        await self.usage_service.update_usage(counter.total_llm_token_count, cost_name)
        await self.usage_service.update_usage(
            counter.total_embedding_token_count, context.embed_model.model_name
        )
        price = await self.usage_service.get_price(
            counter.total_llm_token_count, cost_name
        ) + await self.usage_service.get_price(
            counter.total_embedding_token_count, EnvService.get_openai_embedding_model()
        )
        if ctx:
            await self.try_edit(
                in_progress_message,
                self.build_search_final_embed(refined_query, str(round(price, 6))),
            )
        return response, refined_query
