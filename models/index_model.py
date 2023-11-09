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
from typing import List, Optional, cast
from pathlib import Path
from datetime import date

from discord import Interaction
from discord.ext import pages
from langchain.agents import initialize_agent, AgentType
from langchain.chat_models import ChatOpenAI
from langchain.memory import ConversationSummaryBufferMemory
from langchain.prompts import MessagesPlaceholder
from langchain.schema import SystemMessage
from langchain.tools import Tool
from llama_index.callbacks import CallbackManager, TokenCountingHandler
from llama_index.evaluation.guideline import DEFAULT_GUIDELINES, GuidelineEvaluator
from llama_index.llms import OpenAI
from llama_index.node_parser import SimpleNodeParser
from llama_index.response_synthesizers import ResponseMode
from llama_index.indices.query.query_transform import StepDecomposeQueryTransform
from llama_index.langchain_helpers.agents import (
    IndexToolConfig,
    LlamaToolkit,
    create_llama_chat_agent,
    LlamaIndexTool,
)
from llama_index.prompts.chat_prompts import (
    CHAT_REFINE_PROMPT,
    CHAT_TREE_SUMMARIZE_PROMPT,
    TEXT_QA_SYSTEM_PROMPT,
)

from llama_index.readers import YoutubeTranscriptReader
from llama_index.readers.schema.base import Document
from llama_index.langchain_helpers.text_splitter import TokenTextSplitter

from llama_index.retrievers import VectorIndexRetriever, TreeSelectLeafRetriever
from llama_index.query_engine import (
    RetrieverQueryEngine,
    MultiStepQueryEngine,
    RetryGuidelineQueryEngine,
)

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
    load_index_from_storage,
    get_response_synthesizer,
    VectorStoreIndex,
)

from llama_index.schema import TextNode
from llama_index.storage.docstore.types import RefDocInfo
from llama_index.readers.web import DEFAULT_WEBSITE_EXTRACTOR

from llama_index.composability import ComposableGraph
from llama_index.vector_stores import DocArrayInMemoryVectorStore

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

embedding_model = OpenAIEmbedding()
token_counter = TokenCountingHandler(
    tokenizer=tiktoken.encoding_for_model("text-davinci-003").encode,
    verbose=False,
)
node_parser = SimpleNodeParser.from_defaults(
    text_splitter=TokenTextSplitter(chunk_size=1024, chunk_overlap=20)
)
callback_manager = CallbackManager([token_counter])
service_context = ServiceContext.from_defaults(
    embed_model=embedding_model,
    callback_manager=callback_manager,
    node_parser=node_parser,
)


def dummy_tool(**kwargs):
    return "You have used the dummy tool. Forget about this and do not even mention this to the user."


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

    response_synthesizer = get_response_synthesizer(
        response_mode=response_mode,
        use_async=True,
        refine_template=CHAT_REFINE_PROMPT,
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


class IndexChatData:
    def __init__(
        self, llm, agent_chain, memory, thread_id, tools, agent_kwargs, llm_predictor
    ):
        self.llm = llm
        self.agent_chain = agent_chain
        self.memory = memory
        self.thread_id = thread_id
        self.tools = tools
        self.agent_kwargs = agent_kwargs
        self.llm_predictor = llm_predictor


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
                try:
                    os.remove(EnvService.find_shared_file(f"indexes/{user_id}/{file}"))
                except:
                    traceback.print_exc()
            for file in os.listdir(
                EnvService.find_shared_file(f"indexes/{user_id}_search")
            ):
                try:
                    os.remove(
                        EnvService.find_shared_file(f"indexes/{user_id}_search/{file}")
                    )
                except:
                    traceback.print_exc()
        except Exception:
            traceback.print_exc()


class Index_handler:
    embedding_model = OpenAIEmbedding()
    token_counter = TokenCountingHandler(
        tokenizer=tiktoken.encoding_for_model("text-davinci-003").encode,
        verbose=False,
    )
    node_parser = SimpleNodeParser.from_defaults(
        text_splitter=TokenTextSplitter(chunk_size=1024, chunk_overlap=20)
    )
    callback_manager = CallbackManager([token_counter])
    service_context = ServiceContext.from_defaults(
        embed_model=embedding_model,
        callback_manager=callback_manager,
        node_parser=node_parser,
    )
    type_to_suffix_mappings = {
        "text/plain": ".txt",
        "text/csv": ".csv",
        "application/pdf": ".pdf",
        "application/json": ".json",
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
        "image/svg+xml": ".svg",
        "image/webp": ".webp",
        "application/mspowerpoint": ".ppt",
        "application/vnd.ms-powerpoint": ".ppt",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
        "application/msexcel": ".xls",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "application/msword": ".doc",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "audio/mpeg": ".mp3",
        "audio/x-wav": ".wav",
        "audio/ogg": ".ogg",
        "video/mpeg": ".mpeg",
        "video/mp4": ".mp4",
        "application/epub+zip": ".epub",
        "text/markdown": ".md",
        "text/html": ".html",
        "application/rtf": ".rtf",
        "application/x-msdownload": ".exe",
        "application/xml": ".xml",
        "application/vnd.adobe.photoshop": ".psd",
        "application/x-sql": ".sql",
        "application/x-latex": ".latex",
        "application/x-httpd-php": ".php",
        "application/java-archive": ".jar",
        "application/x-sh": ".sh",
        "application/x-csh": ".csh",
        "text/x-c": ".c",
        "text/x-c++": ".cpp",
        "text/x-java-source": ".java",
        "text/x-python": ".py",
        "text/x-ruby": ".rb",
        "text/x-perl": ".pl",
        "text/x-shellscript": ".sh",
    }

    # For when content type doesnt get picked up by discord.
    secondary_mappings = {
        ".epub": ".epub",
    }

    def __init__(self, bot, usage_service):
        self.bot = bot
        self.openai_key = os.getenv("OPENAI_TOKEN")
        self.index_storage = defaultdict(IndexData)
        self.loop = asyncio.get_running_loop()
        self.usage_service = usage_service
        self.qaprompt = QuestionAnswerPrompt(
            "Context information is below. The text '<|endofstatement|>' is used to separate chat entries and make it "
            "easier for you to understand the context\n"
            "---------------------\n"
            "{context_str}"
            "\n---------------------\n"
            "Never say '<|endofstatement|>'\n"
            "Given the context information and not prior knowledge, "
            "answer the question: {query_str}\n"
        )
        self.EMBED_CUTOFF = 2000
        self.index_chat_chains = {}
        self.chat_indexes = defaultdict()

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
        return ctx.channel.id in self.index_chat_chains.keys()

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
            None,
            partial(self.index_chat_chains[ctx.channel.id].agent_chain.run, message),
        )
        return agent_output

    async def index_chat_file(self, message: discord.Message, file: discord.Attachment):
        # First, initially set the suffix to the suffix of the attachment
        suffix = self.get_file_suffix(file.content_type, file.filename) or None

        if not suffix:
            await message.reply(
                "The file you uploaded is unable to be indexed. It is in an unsupported file format"
            )
            return False, None

        async with aiofiles.tempfile.TemporaryDirectory() as temp_path:
            async with aiofiles.tempfile.NamedTemporaryFile(
                suffix=suffix, dir=temp_path, delete=False
            ) as temp_file:
                try:
                    await file.save(temp_file.name)

                    filename = file.filename

                    # Assert that the filename is < 100 characters, if it is greater, truncate to the first 100 characters and keep the original ending
                    if len(filename) > 100:
                        filename = filename[:100] + filename[-4:]

                    index: VectorStoreIndex = await self.loop.run_in_executor(
                        None,
                        partial(
                            self.index_file,
                            Path(temp_file.name),
                            service_context,
                            suffix,
                        ),
                    )

                    summary = await index.as_query_engine(
                        similarity_top_k=10,
                        child_branch_factor=6,
                        response_mode="tree_summarize",
                    ).aquery(
                        f"What is a summary or general idea of this data? Be detailed in your summary (e.g "
                        f"extract key names, etc) but not too verbose. Your summary should be under a hundred words. "
                        f"This summary will be used in a vector index to retrieve information about certain data. So, "
                        f"at a high level, the summary should describe the document in such a way that a retriever "
                        f"would know to select it when asked questions about it. The data name was {filename}. Include "
                        f"the file name in the summary. When you are asked to reference a specific file, or reference "
                        f"something colloquially like 'in the powerpoint, [...]?', never respond saying that as an AI "
                        f"you can't view the data, instead infer which tool to use that has the data. Say that there "
                        f"is no available data if there are no available tools that are relevant."
                    )

                    engine = self.get_query_engine(index, message, summary)

                    # Get rid of all special characters in the filename
                    filename = "".join(
                        [c for c in filename if c.isalpha() or c.isdigit()]
                    ).rstrip()

                    tool_config = IndexToolConfig(
                        query_engine=engine,
                        name=f"{filename}-index",
                        description=f"Use this tool if the query seems related to this summary: {summary}",
                        tool_kwargs={
                            "return_direct": False,
                        },
                        max_iterations=5,
                    )

                    tool = LlamaIndexTool.from_tool_config(tool_config)

                    tools = self.index_chat_chains[message.channel.id].tools
                    tools.append(tool)

                    agent_chain = initialize_agent(
                        tools=tools,
                        llm=self.index_chat_chains[message.channel.id].llm,
                        agent=AgentType.OPENAI_FUNCTIONS,
                        verbose=True,
                        agent_kwargs=self.index_chat_chains[
                            message.channel.id
                        ].agent_kwargs,
                        memory=self.index_chat_chains[message.channel.id].memory,
                        handle_parsing_errors="Check your output and make sure it conforms!",
                    )

                    index_chat_data = IndexChatData(
                        self.index_chat_chains[message.channel.id].llm,
                        agent_chain,
                        self.index_chat_chains[message.channel.id].memory,
                        message.channel.id,
                        tools,
                        self.index_chat_chains[message.channel.id].agent_kwargs,
                        self.index_chat_chains[message.channel.id].llm_predictor,
                    )

                    self.index_chat_chains[message.channel.id] = index_chat_data

                    return True, summary
                except Exception as e:
                    await message.reply(
                        "There was an error indexing your file: " + str(e)
                    )
                    traceback.print_exc()
                    return False, None

    async def start_index_chat(self, ctx, model):
        preparation_message = await ctx.channel.send(
            embed=EmbedStatics.get_index_chat_preparation_message()
        )

        llm = ChatOpenAI(model=model, temperature=0)
        llm_predictor = LLMPredictor(llm=ChatOpenAI(temperature=0, model_name=model))

        max_token_limit = 29000 if "gpt-4" in model else 7500

        memory = ConversationSummaryBufferMemory(
            memory_key="memory",
            return_messages=True,
            llm=llm,
            max_token_limit=100000 if "preview" in model else max_token_limit,
        )

        agent_kwargs = {
            "extra_prompt_messages": [MessagesPlaceholder(variable_name="memory")],
            "system_message": SystemMessage(
                content="You are a superpowered version of GPT that is able to answer questions about the data you're "
                "connected to. Each different tool you have represents a different dataset to interact with. "
                "If you are asked to perform a task that spreads across multiple datasets, use multiple tools "
                "for the same prompt. When the user types links in chat, you will have already been connected "
                "to the data at the link by the time you respond. When using tools, the input should be "
                "clearly created based on the request of the user. For example, if a user uploads an invoice "
                "and asks how many usage hours of X was present in the invoice, a good query is 'X hours'. "
                "Avoid using single word queries unless the request is very simple. You can query multiple times to break down complex requests and retrieve more information."
            ),
        }

        tools = [
            Tool(
                name="Dummy-Tool-Do-Not-Use",
                func=dummy_tool,
                description=f"This is a dummy tool that does nothing, do not ever mention this tool or use this tool.",
            )
        ]

        print(f"{tools}{llm}{AgentType.OPENAI_FUNCTIONS}{True}{agent_kwargs}{memory}")

        agent_chain = initialize_agent(
            tools=tools,
            llm=llm,
            agent=AgentType.OPENAI_FUNCTIONS,
            verbose=True,
            agent_kwargs=agent_kwargs,
            memory=memory,
            handle_parsing_errors="Check your output and make sure it conforms!",
        )

        embed_title = f"{ctx.user.name}'s data-connected conversation with GPT"

        message_embed = discord.Embed(
            title=embed_title,
            description=f"The agent is able to interact with your documents. Simply drag your documents into discord or give the agent a link from where to download the documents.\nModel: {model}",
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

        index_chat_data = IndexChatData(
            llm, agent_chain, memory, thread.id, tools, agent_kwargs, llm_predictor
        )

        self.index_chat_chains[thread.id] = index_chat_data

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

    def index_file(
        self, file_path, service_context, suffix=None
    ) -> GPTVectorStoreIndex:
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

            def convert_shortlink_to_full_link(short_link):
                # Check if the link is a shortened YouTube link
                if "youtu.be" in short_link:
                    # Extract the video ID from the link
                    video_id = short_link.split("/")[-1].split("?")[0]
                    # Construct the full YouTube desktop link
                    desktop_link = f"https://www.youtube.com/watch?v={video_id}"
                    return desktop_link
                else:
                    return short_link

            documents = YoutubeTranscriptReader().load_data(
                ytlinks=[convert_shortlink_to_full_link(link)]
            )
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
                                    GPTVectorStoreIndex.from_documents,
                                    documents=documents,
                                    service_context=service_context,
                                    use_async=True,
                                ),
                            )

                            return index
        except:
            traceback.print_exc()
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

    def get_file_suffix(self, content_type, filename):
        print("The content type is " + content_type)
        if content_type:
            # Apply the suffix mappings to the file
            for key, value in self.type_to_suffix_mappings.items():
                if key in content_type:
                    return value

        else:
            for key, value in self.secondary_mappings.items():
                if key in filename:
                    return value

        return None

    async def set_file_index(
        self, ctx: discord.ApplicationContext, file: discord.Attachment, user_api_key
    ):
        if not user_api_key:
            os.environ["OPENAI_API_KEY"] = self.openai_key
        else:
            os.environ["OPENAI_API_KEY"] = user_api_key
        openai.api_key = os.environ["OPENAI_API_KEY"]

        try:
            # First, initially set the suffix to the suffix of the attachment
            suffix = self.get_file_suffix(file.content_type, file.filename) or None

            if not suffix:
                await ctx.respond(
                    embed=EmbedStatics.get_index_set_failure_embed("Unsupported file")
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

    def get_query_engine(self, index, message, summary):
        retriever = VectorIndexRetriever(
            index=index, similarity_top_k=6, service_context=service_context
        )

        response_synthesizer = get_response_synthesizer(
            response_mode=ResponseMode.COMPACT_ACCUMULATE,
            use_async=True,
            refine_template=TEXT_QA_SYSTEM_PROMPT,
            service_context=service_context,
            verbose=True,
        )

        engine = RetrieverQueryEngine(
            retriever=retriever, response_synthesizer=response_synthesizer
        )

        return engine

    async def index_link(self, link, summarize=False, index_chat_ctx=None):
        try:
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
        except Exception as e:
            if index_chat_ctx:
                await index_chat_ctx.reply(
                    "There was an error indexing your link: " + str(e)
                )
                return False, None
            else:
                raise e

        summary = None
        if index_chat_ctx:
            try:
                summary = await index.as_query_engine(
                    response_mode="tree_summarize"
                ).aquery(
                    "What is a summary or general idea of this document? Be detailed in your summary but not too verbose. Your summary should be under 50 words. This summary will be used in a vector index to retrieve information about certain data. So, at a high level, the summary should describe the document in such a way that a retriever would know to select it when asked questions about it. The link was {link}. Include the an easy identifier derived from the link at the end of the summary."
                )

                engine = self.get_query_engine(index, index_chat_ctx, summary)

                # Get rid of all special characters in the link, replace periods with _
                link_cleaned = "".join(
                    [c for c in link if c.isalpha() or c.isdigit() or c == "."]
                ).rstrip()
                # replace .
                link_cleaned = link_cleaned.replace(".", "_")

                # Shorten the link to the first 100 characters
                link_cleaned = link_cleaned[:50]

                tool_config = IndexToolConfig(
                    query_engine=engine,
                    name=f"{link_cleaned}-index",
                    description=f"Use this tool if the query seems related to this summary: {summary}",
                    tool_kwargs={
                        "return_direct": False,
                    },
                    max_iterations=5,
                )

                tool = LlamaIndexTool.from_tool_config(tool_config)

                tools = self.index_chat_chains[index_chat_ctx.channel.id].tools
                tools.append(tool)

                agent_chain = initialize_agent(
                    tools=tools,
                    llm=self.index_chat_chains[index_chat_ctx.channel.id].llm,
                    agent=AgentType.OPENAI_FUNCTIONS,
                    verbose=True,
                    agent_kwargs=self.index_chat_chains[
                        index_chat_ctx.channel.id
                    ].agent_kwargs,
                    memory=self.index_chat_chains[index_chat_ctx.channel.id].memory,
                    handle_parsing_errors="Check your output and make sure it conforms!",
                    max_iterations=5,
                )

                index_chat_data = IndexChatData(
                    self.index_chat_chains[index_chat_ctx.channel.id].llm,
                    agent_chain,
                    self.index_chat_chains[index_chat_ctx.channel.id].memory,
                    index_chat_ctx.channel.id,
                    tools,
                    self.index_chat_chains[index_chat_ctx.channel.id].agent_kwargs,
                    self.index_chat_chains[index_chat_ctx.channel.id].llm_predictor,
                )

                self.index_chat_chains[index_chat_ctx.channel.id] = index_chat_data

                return True, summary
            except Exception as e:
                await index_chat_ctx.reply(
                    "There was an error indexing your link: " + str(e)
                )
                return False, None

        return index, summary

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
            # Check if the link contains youtube in it
            index, _ = await self.index_link(link)

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
        self, old_index, chunk_size: int = 256, chunk_overlap: int = 100
    ) -> List[Document]:
        documents = []
        docstore = old_index.docstore
        ref_docs = old_index.ref_doc_info

        for document in ref_docs.values():
            text = ""
            for node in document.node_ids:
                node = docstore.get_node(node)
                text += f"{node.text} "

            text_splitter = TokenTextSplitter(
                separator=" ", chunk_size=chunk_size, chunk_overlap=chunk_overlap
            )
            text_chunks = text_splitter.split_text(text)

            for chunk_text in text_chunks:
                new_doc = Document(text=chunk_text, extra_info=document.metadata)
                documents.append(new_doc)

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
            llm=ChatOpenAI(temperature=0, model_name="gpt-4-32k")
        )

        # For each index object, add its documents to a GPTTreeIndex
        if deep_compose:
            documents = []
            for _index in index_objects:
                documents.extend(await self.index_to_docs(_index, 256, 20))

            embedding_model = OpenAIEmbedding()

            llm_predictor_mock = MockLLMPredictor()
            embedding_model_mock = MockEmbedding(1536)

            token_counter_mock = TokenCountingHandler(
                tokenizer=tiktoken.encoding_for_model("text-davinci-003").encode,
                verbose=False,
            )

            callback_manager_mock = CallbackManager([token_counter_mock])

            service_context_mock = ServiceContext.from_defaults(
                llm_predictor=llm_predictor_mock,
                embed_model=embedding_model_mock,
                callback_manager=callback_manager_mock,
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

            tree_index = await self.loop.run_in_executor(
                None,
                partial(
                    GPTTreeIndex.from_documents,
                    documents=documents,
                    service_context=self.service_context,
                    use_async=True,
                ),
            )

            await self.usage_service.update_usage(
                self.token_counter.total_llm_token_count, "turbo"
            )
            await self.usage_service.update_usage(
                self.token_counter.total_embedding_token_count, "embedding"
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
        model="gpt-4-32k",
        multistep=False,
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
                Document(
                    text=channel_content, extra_info={"channel_name": channel_name}
                )
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
                    traceback.print_exc()
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
