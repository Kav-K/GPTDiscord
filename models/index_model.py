import functools
import os
import random
import tempfile
import traceback
import asyncio
from collections import defaultdict

import aiohttp
import discord
import aiofiles
import openai
import tiktoken
from functools import partial
from typing import List, Optional
from pathlib import Path
from datetime import date

from discord import InteractionResponse, Interaction
from discord.ext import pages
from langchain import OpenAI
from langchain.chat_models import ChatOpenAI
from langchain.llms import OpenAIChat
from langchain.memory import ConversationBufferMemory
from llama_index.callbacks import CallbackManager, TokenCountingHandler
from llama_index.data_structs.data_structs import Node
from llama_index.data_structs.node import DocumentRelationship
from llama_index.indices.query.query_transform import StepDecomposeQueryTransform
from llama_index.langchain_helpers.agents import (
    IndexToolConfig,
    LlamaToolkit,
    create_llama_chat_agent,
)
from llama_index.optimization import SentenceEmbeddingOptimizer
from llama_index.prompts.chat_prompts import CHAT_REFINE_PROMPT

from llama_index.readers import YoutubeTranscriptReader
from llama_index.readers.schema.base import Document
from llama_index.langchain_helpers.text_splitter import TokenTextSplitter

from llama_index.retrievers import VectorIndexRetriever, TreeSelectLeafRetriever
from llama_index.query_engine import RetrieverQueryEngine, MultiStepQueryEngine

from llama_index import (
    GPTVectorStoreIndex,
    SimpleDirectoryReader,
    QuestionAnswerPrompt,
    BeautifulSoupWebReader,
    GPTTreeIndex,
    GoogleDocsReader,
    MockLLMPredictor,
    OpenAIEmbedding,
    GithubRepositoryReader,
    MockEmbedding,
    download_loader,
    LLMPredictor,
    ServiceContext,
    StorageContext,
    ResponseSynthesizer,
    load_index_from_storage,
)
from llama_index.readers.web import DEFAULT_WEBSITE_EXTRACTOR

from llama_index.composability import ComposableGraph
from llama_index.schema import BaseDocument

from models.embed_statics_model import EmbedStatics
from models.openai_model import Models
from models.check_model import UrlCheck
from services.environment_service import EnvService

SHORT_TO_LONG_CACHE = {}
MAX_DEEP_COMPOSE_PRICE = EnvService.get_max_deep_compose_price()
EpubReader = download_loader("EpubReader")
MarkdownReader = download_loader("MarkdownReader")
RemoteReader = download_loader("RemoteReader")
RemoteDepthReader = download_loader("RemoteDepthReader")


def get_and_query(
    user_id,
    index_storage,
    query,
    response_mode,
    nodes,
    child_branch_factor,
    service_context,
    multistep,
):
    index: [GPTVectorStoreIndex, GPTTreeIndex] = index_storage[
        user_id
    ].get_index_or_throw()

    if isinstance(index, GPTTreeIndex):
        retriever = TreeSelectLeafRetriever(
            index=index,
            child_branch_factor=child_branch_factor,
            service_context=service_context,
        )
    else:
        retriever = VectorIndexRetriever(
            index=index, similarity_top_k=nodes, service_context=service_context
        )

    response_synthesizer = ResponseSynthesizer.from_args(
        response_mode=response_mode,
        use_async=True,
        refine_template=CHAT_REFINE_PROMPT,
        optimizer=SentenceEmbeddingOptimizer(threshold_cutoff=0.7),
        service_context=service_context,
    )

    query_engine = RetrieverQueryEngine(
        retriever=retriever, response_synthesizer=response_synthesizer
    )

    multistep_query_engine = MultiStepQueryEngine(
        query_engine=query_engine,
        query_transform=StepDecomposeQueryTransform(multistep),
        index_summary="Provides information about everything you need to know about this topic, use this to answer the question.",
    )

    if multistep:
        response = multistep_query_engine.query(query)
    else:
        response = query_engine.query(query)

    return response


class IndexData:
    def __init__(self):
        self.queryable_index = None
        self.individual_indexes = []

    # A safety check for the future
    def get_index_or_throw(self):
        if not self.queryable():
            raise Exception(
                "An index access was attempted before an index was created. This is a programmer error, please report this to the maintainers."
            )
        return self.queryable_index

    def queryable(self):
        return self.queryable_index is not None

    def has_indexes(self, user_id):
        try:
            return (
                len(os.listdir(EnvService.find_shared_file(f"indexes/{user_id}"))) > 0
            )
        except Exception:
            return False

    def has_search_indexes(self, user_id):
        try:
            return (
                len(
                    os.listdir(EnvService.find_shared_file(f"indexes/{user_id}_search"))
                )
                > 0
            )
        except Exception:
            return False

    def add_index(self, index, user_id, file_name):
        self.individual_indexes.append(index)
        self.queryable_index = index

        # Create a folder called "indexes/{USER_ID}" if it doesn't exist already
        Path(f"{EnvService.save_path()}/indexes/{user_id}").mkdir(
            parents=True, exist_ok=True
        )
        # Save the index to file under the user id
        file = f"{date.today().month}_{date.today().day}_{file_name}"
        # If file is > 93 in length, cut it off to 93
        if len(file) > 93:
            file = file[:93]

        index.storage_context.persist(
            persist_dir=EnvService.save_path()
            / "indexes"
            / f"{str(user_id)}"
            / f"{file}"
        )

    def reset_indexes(self, user_id):
        self.individual_indexes = []
        self.queryable_index = None

        # Delete the user indexes
        try:
            # First, clear all the files inside it
            for file in os.listdir(EnvService.find_shared_file(f"indexes/{user_id}")):
                os.remove(EnvService.find_shared_file(f"indexes/{user_id}/{file}"))
            for file in os.listdir(
                EnvService.find_shared_file(f"indexes/{user_id}_search")
            ):
                os.remove(
                    EnvService.find_shared_file(f"indexes/{user_id}_search/{file}")
                )
        except Exception:
            traceback.print_exc()


class Index_handler:
    def __init__(self, bot, usage_service):
        self.bot = bot
        self.openai_key = os.getenv("OPENAI_TOKEN")
        self.index_storage = defaultdict(IndexData)
        self.loop = asyncio.get_running_loop()
        self.usage_service = usage_service
        self.qaprompt = QuestionAnswerPrompt(
            "Context information is below. The text '<|endofstatement|>' is used to separate chat entries and make it easier for you to understand the context\n"
            "---------------------\n"
            "{context_str}"
            "\n---------------------\n"
            "Never say '<|endofstatement|>'\n"
            "Given the context information and not prior knowledge, "
            "answer the question: {query_str}\n"
        )
        self.EMBED_CUTOFF = 2000
        self.index_chat_chains = {}

    async def rename_index(self, ctx, original_path, rename_path):
        """Command handler to rename a user index"""

        index_file = EnvService.find_shared_file(original_path)
        if not index_file:
            return False

        # Rename the file at f"indexes/{ctx.user.id}/{user_index}" to f"indexes/{ctx.user.id}/{new_name}" using Pathlib
        try:
            Path(original_path).rename(rename_path)
            return True
        except Exception as e:
            traceback.print_exc()
            return False

    async def get_is_in_index_chat(self, ctx):
        return ctx.channel.id in self.index_chat_chains

    async def execute_index_chat_message(self, ctx, message):
        if ctx.channel.id not in self.index_chat_chains:
            return None

        if message.lower() in ["stop", "end", "quit", "exit"]:
            await ctx.reply("Ending chat session.")
            self.index_chat_chains.pop(ctx.channel.id)

            # close the thread
            thread = await self.bot.fetch_channel(ctx.channel.id)
            await thread.edit(name="Closed-GPT")
            await thread.edit(archived=True)
            return "Ended chat session."

        agent_output = await self.loop.run_in_executor(
            None, partial(self.index_chat_chains[ctx.channel.id].run, message)
        )
        return agent_output

    async def start_index_chat(self, ctx, search, user, model):
        if search:
            index_file = EnvService.find_shared_file(
                f"indexes/{ctx.user.id}_search/{search}"
            )
        elif user:
            index_file = EnvService.find_shared_file(f"indexes/{ctx.user.id}/{user}")

        assert index_file is not None

        preparation_message = await ctx.channel.send(
            embed=EmbedStatics.get_index_chat_preparation_message()
        )

        index = await self.loop.run_in_executor(
            None, partial(self.index_load_file, index_file)
        )

        summary_response = await self.loop.run_in_executor(
            None,
            partial(
                index.as_query_engine().query, "What is a summary of this document?"
            ),
        )

        query_engine = index.as_query_engine(similarity_top_k=3)

        tool_config = IndexToolConfig(
            query_engine=query_engine,
            name=f"Vector Index",
            description=f"useful for when you want to answer queries about the external data you're connected to. The data you're connected to is: {summary_response}",
            tool_kwargs={"return_direct": True},
        )
        toolkit = LlamaToolkit(
            index_configs=[tool_config],
        )
        memory = ConversationBufferMemory(memory_key="chat_history")
        llm = ChatOpenAI(model=model, temperature=0)
        agent_chain = create_llama_chat_agent(toolkit, llm, memory=memory, verbose=True)

        embed_title = f"{ctx.user.name}'s data-connected conversation with GPT"
        # Get only the last part after the last / of the index_file
        try:
            index_file_name = str(index_file).split("/")[-1]
        except:
            index_file_name = index_file

        message_embed = discord.Embed(
            title=embed_title,
            description=f"The agent is connected to the data index named {index_file_name}\nModel: {model}",
            color=0x00995B,
        )
        message_embed.set_thumbnail(url="https://i.imgur.com/7V6apMT.png")
        message_embed.set_footer(
            text="Data Chat", icon_url="https://i.imgur.com/7V6apMT.png"
        )
        message_thread = await ctx.send(embed=message_embed)
        thread = await message_thread.create_thread(
            name=ctx.user.name + "'s data-connected conversation with GPT",
            auto_archive_duration=60,
        )
        await ctx.respond("Conversation started.")

        try:
            await preparation_message.delete()
        except:
            pass

        self.index_chat_chains[thread.id] = agent_chain

    async def paginate_embed(self, response_text):
        """Given a response text make embed pages and return a list of the pages."""

        response_text = [
            response_text[i : i + self.EMBED_CUTOFF]
            for i in range(0, len(response_text), self.EMBED_CUTOFF)
        ]
        pages = []
        first = False
        # Send each chunk as a message
        for count, chunk in enumerate(response_text, start=1):
            if not first:
                page = discord.Embed(
                    title=f"Index Query Results",
                    description=chunk,
                )
                first = True
            else:
                page = discord.Embed(
                    title=f"Page {count}",
                    description=chunk,
                )
            pages.append(page)

        return pages

    def index_file(self, file_path, service_context, suffix=None) -> GPTVectorStoreIndex:
        if suffix and suffix == ".md":
            loader = MarkdownReader()
            document = loader.load_data(file_path)
        elif suffix and suffix == ".epub":
            epub_loader = EpubReader()
            document = epub_loader.load_data(file_path)
        else:
            document = SimpleDirectoryReader(input_files=[file_path]).load_data()
        index = GPTVectorStoreIndex.from_documents(
            document, service_context=service_context, use_async=True
        )
        return index

    def index_gdoc(self, doc_id, service_context) -> GPTVectorStoreIndex:
        document = GoogleDocsReader().load_data(doc_id)
        index = GPTVectorStoreIndex.from_documents(
            document, service_context=service_context, use_async=True
        )
        return index

    def index_youtube_transcript(self, link, service_context):
        try:
            documents = YoutubeTranscriptReader().load_data(ytlinks=[link])
        except Exception as e:
            raise ValueError(f"The youtube transcript couldn't be loaded: {e}")

        index = GPTVectorStoreIndex.from_documents(
            documents,
            service_context=service_context,
            use_async=True,
        )
        return index

    def index_github_repository(self, link, service_context):
        # Extract the "owner" and the "repo" name from the github link.
        owner = link.split("/")[3]
        repo = link.split("/")[4]

        try:
            documents = GithubRepositoryReader(owner=owner, repo=repo).load_data(
                branch="main"
            )
        except KeyError:
            documents = GithubRepositoryReader(owner=owner, repo=repo).load_data(
                branch="master"
            )

        index = GPTVectorStoreIndex.from_documents(
            documents,
            service_context=service_context,
            use_async=True,
        )
        return index

    def index_load_file(self, file_path) -> [GPTVectorStoreIndex, ComposableGraph]:
        storage_context = StorageContext.from_defaults(persist_dir=file_path)
        index = load_index_from_storage(storage_context)
        return index

    def index_discord(self, document, service_context) -> GPTVectorStoreIndex:
        index = GPTVectorStoreIndex.from_documents(
            document,
            service_context=service_context,
            use_async=True,
        )
        return index

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

        # Delete the temporary file
        return documents

    async def index_webpage(self, url, service_context) -> GPTVectorStoreIndex:
        # First try to connect to the URL to see if we can even reach it.
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=5) as response:
                    # Add another entry to links from all_links if the link is not already in it to compensate for the failed request
                    if response.status not in [200, 203, 202, 204]:
                        raise ValueError(
                            "Invalid URL or could not connect to the provided URL."
                        )
                    else:
                        # Detect if the link is a PDF, if it is, we load it differently
                        if response.headers["Content-Type"] == "application/pdf":
                            documents = await self.index_pdf(url)
                            index = await self.loop.run_in_executor(
                                None,
                                functools.partial(
                                    GPTVectorStoreIndex,
                                    documents=documents,
                                    service_context=service_context,
                                    use_async=True,
                                ),
                            )

                            return index
        except:
            raise ValueError("Could not load webpage")

        documents = BeautifulSoupWebReader(
            website_extractor=DEFAULT_WEBSITE_EXTRACTOR
        ).load_data(urls=[url])

        # index = GPTVectorStoreIndex(documents, embed_model=embed_model, use_async=True)
        index = await self.loop.run_in_executor(
            None,
            functools.partial(
                GPTVectorStoreIndex.from_documents,
                documents=documents,
                service_context=service_context,
                use_async=True,
            ),
        )
        return index

    def reset_indexes(self, user_id):
        self.index_storage[user_id].reset_indexes(user_id)

    async def set_file_index(
        self, ctx: discord.ApplicationContext, file: discord.Attachment, user_api_key
    ):
        if not user_api_key:
            os.environ["OPENAI_API_KEY"] = self.openai_key
        else:
            os.environ["OPENAI_API_KEY"] = user_api_key
        openai.api_key = os.environ["OPENAI_API_KEY"]

        type_to_suffix_mappings = {
            "text/plain": ".txt",
            "text/csv": ".csv",
            "application/pdf": ".pdf",
            "application/json": ".json",
            "image/png": ".png",
            "image/": ".jpg",
            "ms-powerpoint": ".ppt",
            "presentationml.presentation": ".pptx",
            "ms-excel": ".xls",
            "spreadsheetml.sheet": ".xlsx",
            "msword": ".doc",
            "wordprocessingml.document": ".docx",
            "audio/": ".mp3",
            "video/": ".mp4",
            "epub": ".epub",
            "markdown": ".md",
            "html": ".html",
        }

        # For when content type doesnt get picked up by discord.
        secondary_mappings = {
            ".epub": ".epub",
        }

        try:
            # First, initially set the suffix to the suffix of the attachment
            suffix = None
            if file.content_type:
                # Apply the suffix mappings to the file
                for key, value in type_to_suffix_mappings.items():
                    if key in file.content_type:
                        suffix = value
                        break

                if not suffix:
                    await ctx.send("This file type is not supported.")
                    return

            else:
                for key, value in secondary_mappings.items():
                    if key in file.filename:
                        suffix = value
                        break
                if not suffix:
                    await ctx.send(
                        "Could not determine the file type of the attachment, attempting a dirty index.."
                    )
                    return

            # Send indexing message
            response = await ctx.respond(
                embed=EmbedStatics.build_index_progress_embed()
            )

            async with aiofiles.tempfile.TemporaryDirectory() as temp_path:
                async with aiofiles.tempfile.NamedTemporaryFile(
                    suffix=suffix, dir=temp_path, delete=False
                ) as temp_file:
                    await file.save(temp_file.name)
                    embedding_model = OpenAIEmbedding()
                    token_counter = TokenCountingHandler(
                        tokenizer=tiktoken.encoding_for_model("text-davinci-003").encode,
                        verbose=False
                    )
                    callback_manager = CallbackManager([token_counter])
                    service_context = ServiceContext.from_defaults(embed_model=embedding_model, callback_manager=callback_manager)
                    index = await self.loop.run_in_executor(
                        None,
                        partial(
                            self.index_file,
                            Path(temp_file.name),
                            service_context,
                            suffix,
                        ),
                    )
                    await self.usage_service.update_usage(
                        token_counter.total_embedding_token_count, "embedding"
                    )

            try:
                price = await self.usage_service.get_price(
                    token_counter.total_embedding_token_count, "embedding"
                )
            except:
                traceback.print_exc()
                price = "Unknown"

            file_name = file.filename
            self.index_storage[ctx.user.id].add_index(index, ctx.user.id, file_name)
            await response.edit(
                embed=EmbedStatics.get_index_set_success_embed(str(price))
            )
        except Exception as e:
            await ctx.channel.send(
                embed=EmbedStatics.get_index_set_failure_embed(str(e))
            )
            traceback.print_exc()

    async def set_link_index_recurse(
        self, ctx: discord.ApplicationContext, link: str, depth, user_api_key
    ):
        if not user_api_key:
            os.environ["OPENAI_API_KEY"] = self.openai_key
        else:
            os.environ["OPENAI_API_KEY"] = user_api_key
        openai.api_key = os.environ["OPENAI_API_KEY"]

        response = await ctx.respond(embed=EmbedStatics.build_index_progress_embed())
        try:
            embedding_model = OpenAIEmbedding()
            token_counter = TokenCountingHandler(
                tokenizer=tiktoken.encoding_for_model("text-davinci-003").encode,
                verbose=False
            )
            callback_manager = CallbackManager([token_counter])
            service_context = ServiceContext.from_defaults(embed_model=embedding_model, callback_manager=callback_manager)

            # Pre-emptively connect and get the content-type of the response
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(link, timeout=2) as _response:
                        print(_response.status)
                        if _response.status == 200:
                            content_type = _response.headers.get("content-type")
                        else:
                            await response.edit(
                                embed=EmbedStatics.get_index_set_failure_embed(
                                    "Invalid URL or could not connect to the provided URL."
                                )
                            )
                            return
            except Exception as e:
                traceback.print_exc()
                await response.edit(
                    embed=EmbedStatics.get_index_set_failure_embed(
                        "Invalid URL or could not connect to the provided URL. "
                        + str(e)
                    )
                )
                return

            # Check if the link contains youtube in it
            loader = RemoteDepthReader(depth=depth)
            documents = await self.loop.run_in_executor(
                None, partial(loader.load_data, [link])
            )
            index = await self.loop.run_in_executor(
                None,
                functools.partial(
                    GPTVectorStoreIndex,
                    documents=documents,
                    service_context=service_context,
                    use_async=True,
                ),
            )

            await self.usage_service.update_usage(
                token_counter.total_embedding_token_count, "embedding"
            )

            try:
                price = await self.usage_service.get_price(
                    token_counter.total_embedding_token_count, "embedding"
                )
            except:
                traceback.print_exc()
                price = "Unknown"

            # Make the url look nice, remove https, useless stuff, random characters
            file_name = (
                link.replace("https://", "")
                .replace("http://", "")
                .replace("www.", "")
                .replace("/", "_")
                .replace("?", "_")
                .replace("&", "_")
                .replace("=", "_")
                .replace("-", "_")
                .replace(".", "_")
            )

            self.index_storage[ctx.user.id].add_index(index, ctx.user.id, file_name)

        except ValueError as e:
            await response.edit(embed=EmbedStatics.get_index_set_failure_embed(str(e)))
            traceback.print_exc()
            return

        except Exception as e:
            await response.edit(embed=EmbedStatics.get_index_set_failure_embed(str(e)))
            traceback.print_exc()
            return

        await response.edit(embed=EmbedStatics.get_index_set_success_embed(price))

    async def set_link_index(
        self, ctx: discord.ApplicationContext, link: str, user_api_key
    ):
        if not user_api_key:
            os.environ["OPENAI_API_KEY"] = self.openai_key
        else:
            os.environ["OPENAI_API_KEY"] = user_api_key
        openai.api_key = os.environ["OPENAI_API_KEY"]

        response = await ctx.respond(embed=EmbedStatics.build_index_progress_embed())
        try:
            embedding_model = OpenAIEmbedding()
            token_counter = TokenCountingHandler(
                tokenizer=tiktoken.encoding_for_model("text-davinci-003").encode,
                verbose=False
            )
            callback_manager = CallbackManager([token_counter])
            service_context = ServiceContext.from_defaults(embed_model=embedding_model, callback_manager=callback_manager)

            # Pre-emptively connect and get the content-type of the response
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(link, timeout=2) as _response:
                        print(_response.status)
                        if _response.status == 200:
                            content_type = _response.headers.get("content-type")
                        else:
                            await response.edit(
                                embed=EmbedStatics.get_index_set_failure_embed(
                                    "Invalid URL or could not connect to the provided URL."
                                )
                            )
                            return
            except Exception as e:
                traceback.print_exc()
                await response.edit(
                    embed=EmbedStatics.get_index_set_failure_embed(
                        "Invalid URL or could not connect to the provided URL. "
                        + str(e)
                    )
                )
                return

            # Check if the link contains youtube in it
            if await UrlCheck.check_youtube_link(link):
                index = await self.loop.run_in_executor(
                    None, partial(self.index_youtube_transcript, link, service_context)
                )
            elif "github" in link:
                index = await self.loop.run_in_executor(
                    None, partial(self.index_github_repository, link, service_context)
                )
            else:
                index = await self.index_webpage(link, service_context)
            await self.usage_service.update_usage(
                token_counter.total_embedding_token_count, "embedding"
            )

            try:
                price = await self.usage_service.get_price(
                    token_counter.embedding_token_counts, "embedding"
                )
            except:
                traceback.print_exc()
                price = "Unknown"

            # Make the url look nice, remove https, useless stuff, random characters
            file_name = (
                link.replace("https://", "")
                .replace("http://", "")
                .replace("www.", "")
                .replace("/", "_")
                .replace("?", "_")
                .replace("&", "_")
                .replace("=", "_")
                .replace("-", "_")
                .replace(".", "_")
            )

            self.index_storage[ctx.user.id].add_index(index, ctx.user.id, file_name)

        except ValueError as e:
            await response.edit(embed=EmbedStatics.get_index_set_failure_embed(str(e)))
            traceback.print_exc()
            return

        except Exception as e:
            await response.edit(embed=EmbedStatics.get_index_set_failure_embed(str(e)))
            traceback.print_exc()
            return

        await response.edit(embed=EmbedStatics.get_index_set_success_embed(price))

    async def set_discord_index(
        self,
        ctx: discord.ApplicationContext,
        channel: discord.TextChannel,
        user_api_key,
        message_limit: int = 2500,
    ):
        if not user_api_key:
            os.environ["OPENAI_API_KEY"] = self.openai_key
        else:
            os.environ["OPENAI_API_KEY"] = user_api_key
        openai.api_key = os.environ["OPENAI_API_KEY"]

        try:
            document = await self.load_data(
                channel_ids=[channel.id], limit=message_limit, oldest_first=False
            )
            embedding_model = OpenAIEmbedding()
            token_counter = TokenCountingHandler(
                tokenizer=tiktoken.encoding_for_model("text-davinci-003").encode,
                verbose=False
            )
            callback_manager = CallbackManager([token_counter])
            service_context = ServiceContext.from_defaults(embed_model=embedding_model, callback_manager=callback_manager)
            index = await self.loop.run_in_executor(
                None, partial(self.index_discord, document, service_context)
            )
            try:
                price = await self.usage_service.get_price(
                    token_counter.total_embedding_token_count, "embedding"
                )
            except Exception:
                traceback.print_exc()
                price = "Unknown"
            await self.usage_service.update_usage(
                token_counter.total_embedding_token_count, "embedding"
            )
            self.index_storage[ctx.user.id].add_index(index, ctx.user.id, channel.name)
            await ctx.respond(embed=EmbedStatics.get_index_set_success_embed(price))
        except Exception as e:
            await ctx.respond(embed=EmbedStatics.get_index_set_failure_embed(str(e)))
            traceback.print_exc()

    async def load_index(
        self, ctx: discord.ApplicationContext, index, server, search, user_api_key
    ):
        if not user_api_key:
            os.environ["OPENAI_API_KEY"] = self.openai_key
        else:
            os.environ["OPENAI_API_KEY"] = user_api_key
        openai.api_key = os.environ["OPENAI_API_KEY"]

        try:
            if server:
                index_file = EnvService.find_shared_file(
                    f"indexes/{ctx.guild.id}/{index}"
                )
            elif search:
                index_file = EnvService.find_shared_file(
                    f"indexes/{ctx.user.id}_search/{index}"
                )
            else:
                index_file = EnvService.find_shared_file(
                    f"indexes/{ctx.user.id}/{index}"
                )
            index = await self.loop.run_in_executor(
                None, partial(self.index_load_file, index_file)
            )
            self.index_storage[ctx.user.id].queryable_index = index
            await ctx.respond(embed=EmbedStatics.get_index_load_success_embed())
        except Exception as e:
            traceback.print_exc()
            await ctx.respond(embed=EmbedStatics.get_index_load_failure_embed(str(e)))

    async def index_to_docs(
        self, old_index, chunk_size: int = 4000, chunk_overlap: int = 200
    ) -> List[BaseDocument]:
        documents = []
        docstore = old_index.docstore

        for doc_id in docstore.docs.keys():
            text = ""

            document = docstore.get_document(doc_id)
            if document is not None:
                node = docstore.get_node(document.get_doc_id())
                while node is not None:
                    extra_info = node.extra_info
                    text += f"{node.text} "
                    next_node_id = node.relationships.get(
                        DocumentRelationship.NEXT, None
                    )
                    node = docstore.get_node(next_node_id) if next_node_id else None

            text_splitter = TokenTextSplitter(
                separator=" ", chunk_size=chunk_size, chunk_overlap=chunk_overlap
            )
            text_chunks = text_splitter.split_text(text)

            for chunk_text in text_chunks:
                new_doc = Document(text=chunk_text, extra_info=extra_info)
                documents.append(new_doc)
                print(new_doc)

        return documents

    async def compose_indexes(self, user_id, indexes, name, deep_compose):
        # Load all the indexes first
        index_objects = []
        for _index in indexes:
            try:
                index_file = EnvService.find_shared_file(f"indexes/{user_id}/{_index}")
            except ValueError:
                index_file = EnvService.find_shared_file(
                    f"indexes/{user_id}_search/{_index}"
                )

            index = await self.loop.run_in_executor(
                None, partial(self.index_load_file, index_file)
            )
            index_objects.append(index)

        llm_predictor = LLMPredictor(
            llm=ChatOpenAI(temperature=0, model_name="gpt-3.5-turbo")
        )

        # For each index object, add its documents to a GPTTreeIndex
        if deep_compose:
            documents = []
            for _index in index_objects:
                documents.extend(await self.index_to_docs(_index, 256, 20))

            embedding_model = OpenAIEmbedding()

            llm_predictor_mock = MockLLMPredictor(4096)
            embedding_model_mock = MockEmbedding(1536)
            
            token_counter_mock = TokenCountingHandler(
                tokenizer=tiktoken.encoding_for_model("text-davinci-003").encode,
                verbose=False
            )

            callback_manager_mock = CallbackManager([token_counter_mock])

            service_context_mock = ServiceContext.from_defaults(
                llm_predictor=llm_predictor_mock, embed_model=embedding_model_mock, callback_manager=callback_manager_mock
            )

            # Run the mock call first
            await self.loop.run_in_executor(
                None,
                partial(
                    GPTTreeIndex.from_documents,
                    documents=documents,
                    service_context=service_context_mock,
                ),
            )
            total_usage_price = await self.usage_service.get_price(
                token_counter_mock.total_llm_token_count,
                "turbo",  # TODO Enable again when tree indexes are fixed
            ) + await self.usage_service.get_price(
                token_counter_mock.total_embedding_token_count, "embedding"
            )
            print("The total composition price is: ", total_usage_price)
            if total_usage_price > MAX_DEEP_COMPOSE_PRICE:
                raise ValueError(
                    "Doing this deep search would be prohibitively expensive. Please try a narrower search scope."
                )

            token_counter = TokenCountingHandler(
                tokenizer=tiktoken.encoding_for_model("gpt-3.5-turbo").encode,
                verbose=False
            )

            callback_manager = CallbackManager([token_counter])

            service_context = ServiceContext.from_defaults(
                llm_predictor=llm_predictor, embed_model=embedding_model, callback_manager=callback_manager
            )

            tree_index = await self.loop.run_in_executor(
                None,
                partial(
                    GPTTreeIndex.from_documents,
                    documents=documents,
                    service_context=service_context,
                    use_async=True,
                ),
            )

            await self.usage_service.update_usage(
                token_counter.total_llm_token_count, "turbo"
            )
            await self.usage_service.update_usage(
                token_counter.total_embedding_token_count, "embedding"
            )

            # Now we have a list of tree indexes, we can compose them
            if not name:
                name = f"{date.today().month}_{date.today().day}_composed_deep_index"

            # Save the composed index
            tree_index.storage_context.persist(
                persist_dir=EnvService.save_path() / "indexes" / str(user_id) / name
            )

            self.index_storage[user_id].queryable_index = tree_index

            return total_usage_price
        else:
            documents = []
            for _index in index_objects:
                documents.extend(await self.index_to_docs(_index))

            embedding_model = OpenAIEmbedding()

            token_counter = TokenCountingHandler(
                tokenizer=tiktoken.encoding_for_model("gpt-3.5-turbo").encode,
                verbose=False
            )

            callback_manager = CallbackManager([token_counter])

            service_context = ServiceContext.from_defaults(embed_model=embedding_model, callback_manager=callback_manager)

            simple_index = await self.loop.run_in_executor(
                None,
                partial(
                    GPTVectorStoreIndex.from_documents,
                    documents=documents,
                    service_context=service_context,
                    use_async=True,
                ),
            )

            await self.usage_service.update_usage(
                token_counter.total_embedding_token_count, "embedding"
            )

            if not name:
                name = f"{date.today().month}_{date.today().day}_composed_index"

            # Save the composed index
            simple_index.storage_context.persist(
                persist_dir=EnvService.save_path() / "indexes" / str(user_id) / name
            )
            self.index_storage[user_id].queryable_index = simple_index

            try:
                price = await self.usage_service.get_price(
                    token_counter.total_embedding_token_count, "embedding"
                )
            except:
                price = "Unknown"

            return price

    async def backup_discord(
        self, ctx: discord.ApplicationContext, user_api_key, message_limit
    ):
        if not user_api_key:
            os.environ["OPENAI_API_KEY"] = self.openai_key
        else:
            os.environ["OPENAI_API_KEY"] = user_api_key
        openai.api_key = os.environ["OPENAI_API_KEY"]

        try:
            channel_ids: List[int] = []
            for c in ctx.guild.text_channels:
                channel_ids.append(c.id)
            document = await self.load_data(
                channel_ids=channel_ids, limit=message_limit, oldest_first=False
            )
            embedding_model = OpenAIEmbedding()
            token_counter = TokenCountingHandler(
                tokenizer=tiktoken.encoding_for_model("text-davinci-003").encode,
                verbose=False
            )
            callback_manager = CallbackManager([token_counter])
            service_context = ServiceContext.from_defaults(embed_model=embedding_model, callback_manager=callback_manager)
            index = await self.loop.run_in_executor(
                None, partial(self.index_discord, document, service_context)
            )
            await self.usage_service.update_usage(
                token_counter.total_embedding_token_count, "embedding"
            )
            try:
                price = await self.usage_service.get_price(
                    token_counter.total_embedding_token_count, "embedding"
                )
            except Exception:
                traceback.print_exc()
                price = "Unknown"
            Path(EnvService.save_path() / "indexes" / str(ctx.guild.id)).mkdir(
                parents=True, exist_ok=True
            )
            index.storage_context.persist(
                persist_dir=EnvService.save_path()
                / "indexes"
                / str(ctx.guild.id)
                / f"{ctx.guild.name.replace(' ', '-')}_{date.today().month}_{date.today().day}"
            )

            await ctx.respond(embed=EmbedStatics.get_index_set_success_embed(price))
        except Exception as e:
            await ctx.respond(embed=EmbedStatics.get_index_set_failure_embed((str(e))))
            traceback.print_exc()

    async def query(
        self,
        ctx: discord.ApplicationContext,
        query: str,
        response_mode,
        nodes,
        user_api_key,
        child_branch_factor,
        model,
        multistep,
    ):
        if not user_api_key:
            os.environ["OPENAI_API_KEY"] = self.openai_key
        else:
            os.environ["OPENAI_API_KEY"] = user_api_key
        openai.api_key = os.environ["OPENAI_API_KEY"]

        llm_predictor = LLMPredictor(llm=ChatOpenAI(temperature=0, model_name=model))

        ctx_response = await ctx.respond(
            embed=EmbedStatics.build_index_query_progress_embed(query)
        )

        try:
            embedding_model = OpenAIEmbedding()
            token_counter = TokenCountingHandler(
                tokenizer=tiktoken.encoding_for_model(model).encode,
                verbose=False
            )

            callback_manager = CallbackManager([token_counter])
            service_context = ServiceContext.from_defaults(
                llm_predictor=llm_predictor, embed_model=embedding_model, callback_manager=callback_manager
            )

            token_counter.reset_counts()
            response = await self.loop.run_in_executor(
                None,
                partial(
                    get_and_query,
                    ctx.user.id,
                    self.index_storage,
                    query,
                    response_mode,
                    nodes,
                    child_branch_factor,
                    service_context=service_context,
                    multistep=llm_predictor if multistep else None,
                ),
            )
            print("The last token usage was ", token_counter.total_llm_token_count)
            await self.usage_service.update_usage(
                token_counter.total_llm_token_count,
                await self.usage_service.get_cost_name(model),
            )
            await self.usage_service.update_usage(
                token_counter.total_embedding_token_count, "embedding"
            )

            try:
                total_price = round(
                    await self.usage_service.get_price(
                        token_counter.total_llm_token_count,
                        await self.usage_service.get_cost_name(model),
                    )
                    + await self.usage_service.get_price(
                        token_counter.total_embedding_token_count, "embedding"
                    ),
                    6,
                )
            except:
                total_price = "Unknown"

            query_response_message = f"**Query:**\n\n`{query.strip()}`\n\n**Query response:**\n\n{response.response.strip()}"
            query_response_message = query_response_message.replace(
                "<|endofstatement|>", ""
            )
            embed_pages = await self.paginate_embed(query_response_message)
            paginator = pages.Paginator(
                pages=embed_pages,
                timeout=None,
                author_check=False,
            )
            await ctx_response.edit(
                embed=EmbedStatics.build_index_query_success_embed(query, total_price)
            )
            await paginator.respond(ctx.interaction)
        except Exception:
            traceback.print_exc()
            await ctx_response.edit(
                embed=EmbedStatics.get_index_query_failure_embed(
                    "Failed to send query. You may not have an index set, load an index with /index load"
                )
            )

    # Extracted functions from DiscordReader

    async def read_channel(
        self, channel_id: int, limit: Optional[int], oldest_first: bool
    ) -> str:
        """Async read channel."""

        messages: List[discord.Message] = []

        try:
            channel = self.bot.get_channel(channel_id)
            print(f"Added {channel.name} from {channel.guild.name}")
            # only work for text channels for now
            if not isinstance(channel, discord.TextChannel):
                raise ValueError(
                    f"Channel {channel_id} is not a text channel. "
                    "Only text channels are supported for now."
                )
            # thread_dict maps thread_id to thread
            thread_dict = {}
            for thread in channel.threads:
                thread_dict[thread.id] = thread

            async for msg in channel.history(limit=limit, oldest_first=oldest_first):
                if msg.author.bot:
                    pass
                else:
                    messages.append(msg)
                    if msg.id in thread_dict:
                        thread = thread_dict[msg.id]
                        async for thread_msg in thread.history(
                            limit=limit, oldest_first=oldest_first
                        ):
                            messages.append(thread_msg)
        except Exception as e:
            print("Encountered error: " + str(e))

        channel = self.bot.get_channel(channel_id)
        msg_txt_list = [
            f"user:{m.author.display_name}, content:{m.content}" for m in messages
        ]

        return ("<|endofstatement|>\n\n".join(msg_txt_list), channel.name)

    async def load_data(
        self,
        channel_ids: List[int],
        limit: Optional[int] = None,
        oldest_first: bool = True,
    ) -> List[Document]:
        """Load data from the input directory.

        Args:
            channel_ids (List[int]): List of channel ids to read.
            limit (Optional[int]): Maximum number of messages to read.
            oldest_first (bool): Whether to read oldest messages first.
                Defaults to `True`.

        Returns:
            List[Document]: List of documents.

        """
        results: List[Document] = []
        for channel_id in channel_ids:
            if not isinstance(channel_id, int):
                raise ValueError(
                    f"Channel id {channel_id} must be an integer, "
                    f"not {type(channel_id)}."
                )
            (channel_content, channel_name) = await self.read_channel(
                channel_id, limit=limit, oldest_first=oldest_first
            )
            results.append(
                Document(channel_content, extra_info={"channel_name": channel_name})
            )
        return results

    async def compose(self, ctx: discord.ApplicationContext, name, user_api_key):
        # Send the ComposeModal
        if not user_api_key:
            os.environ["OPENAI_API_KEY"] = self.openai_key
        else:
            os.environ["OPENAI_API_KEY"] = user_api_key
        openai.api_key = os.environ["OPENAI_API_KEY"]

        if not self.index_storage[ctx.user.id].has_indexes(ctx.user.id):
            await ctx.respond(
                embed=EmbedStatics.get_index_compose_failure_embed(
                    "You must have at least one index to compose."
                )
            )
            return

        await ctx.respond(
            "Select the index(es) to compose. You can compose multiple indexes together, you can also Deep Compose a single index.",
            view=ComposeModal(self, ctx.user.id, name),
            ephemeral=True,
        )


class ComposeModal(discord.ui.View):
    def __init__(self, index_cog, user_id, name=None, deep=None) -> None:
        super().__init__()
        # Get the argument named "user_key_db" and save it as USER_KEY_DB
        self.index_cog = index_cog
        self.user_id = user_id
        self.deep = deep

        # Get all the indexes for the user
        self.indexes = [
            file
            for file in os.listdir(
                EnvService.find_shared_file(f"indexes/{str(user_id)}/")
            )
        ]

        if index_cog.index_storage[user_id].has_search_indexes(user_id):
            self.indexes.extend(
                [
                    file
                    for file in os.listdir(
                        EnvService.find_shared_file(f"indexes/{str(user_id)}_search/")
                    )
                ]
            )
        print("Found the indexes, they are ", self.indexes)

        # Map everything into the short to long cache
        for index in self.indexes:
            if len(index) > 93:
                index_name = index[:93] + "-" + str(random.randint(0000, 9999))
                SHORT_TO_LONG_CACHE[index_name] = index
            else:
                SHORT_TO_LONG_CACHE[index[:99]] = index

        # Reverse the SHORT_TO_LONG_CACHE index
        LONG_TO_SHORT_CACHE = {v: k for k, v in SHORT_TO_LONG_CACHE.items()}

        # A text entry field for the name of the composed index
        self.name = name

        # A discord UI select menu with all the indexes. Limited to 25 entries. For the label field in the SelectOption,
        # cut it off at 100 characters to prevent the message from being too long
        self.index_select = discord.ui.Select(
            placeholder="Select index(es) to compose",
            options=[
                discord.SelectOption(
                    label=LONG_TO_SHORT_CACHE[index], value=LONG_TO_SHORT_CACHE[index]
                )
                for index in self.indexes
            ][0:25],
            max_values=len(self.indexes) if len(self.indexes) < 25 else 25,
            min_values=1,
        )
        # Add the select menu to the modal
        self.add_item(self.index_select)

        # If we have more than 25 entries, add more Select fields as neccessary
        self.extra_index_selects = []
        if len(self.indexes) > 25:
            for i in range(25, len(self.indexes), 25):
                self.extra_index_selects.append(
                    discord.ui.Select(
                        placeholder="Select index(es) to compose",
                        options=[
                            discord.SelectOption(
                                label=LONG_TO_SHORT_CACHE[index],
                                value=LONG_TO_SHORT_CACHE[index],
                            )
                            for index in self.indexes
                        ][i : i + 25],
                        max_values=len(self.indexes[i : i + 25]),
                        min_values=1,
                    )
                )
                self.add_item(self.extra_index_selects[-1])

        # Add an input field for "Deep", a "yes" or "no" option, default no
        self.deep_select = discord.ui.Select(
            placeholder="Deep Compose",
            options=[
                discord.SelectOption(label="Yes", value="yes"),
                discord.SelectOption(label="No", value="no"),
            ],
            max_values=1,
            min_values=1,
        )
        self.add_item(self.deep_select)

        # Add a button to the modal called "Compose"
        self.add_item(
            discord.ui.Button(
                label="Compose", style=discord.ButtonStyle.green, custom_id="compose"
            )
        )

    # The callback for the button
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Check that the interaction was for custom_id "compose"
        if interaction.data["custom_id"] == "compose":
            # Check that the user selected at least one index

            # The total list of indexes is the union of the values of all the select menus
            indexes = self.index_select.values + [
                select.values[0] for select in self.extra_index_selects
            ]

            # Remap them from the SHORT_TO_LONG_CACHE
            indexes = [SHORT_TO_LONG_CACHE[index] for index in indexes]

            if len(indexes) < 1:
                await interaction.response.send_message(
                    embed=EmbedStatics.get_index_compose_failure_embed(
                        "You must select at least 1 index"
                    ),
                    ephemeral=True,
                )
            else:
                composing_message = await interaction.response.send_message(
                    embed=EmbedStatics.get_index_compose_progress_embed(),
                    ephemeral=True,
                )
                # Compose the indexes
                try:
                    price = await self.index_cog.compose_indexes(
                        self.user_id,
                        indexes,
                        self.name,
                        False
                        if not self.deep_select.values
                        or self.deep_select.values[0] == "no"
                        else True,
                    )
                except ValueError as e:
                    await interaction.followup.send(
                        str(e), ephemeral=True, delete_after=180
                    )
                    return False
                except Exception as e:
                    await interaction.followup.send(
                        embed=EmbedStatics.get_index_compose_failure_embed(
                            "An error occurred while composing the indexes: " + str(e)
                        ),
                        ephemeral=True,
                        delete_after=180,
                    )
                    return False

                await interaction.followup.send(
                    embed=EmbedStatics.get_index_compose_success_embed(price),
                    ephemeral=True,
                    delete_after=180,
                )

                # Try to direct message the user that their composed index is ready
                try:
                    await self.index_cog.bot.get_user(self.user_id).send(
                        f"Your composed index is ready! You can load it with /index load now in the server."
                    )
                except discord.Forbidden:
                    pass

                try:
                    composing_message: Interaction
                    await composing_message.delete_original_response()

                except:
                    traceback.print_exc()
        else:
            await interaction.response.defer(ephemeral=True)
