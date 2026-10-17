import functools
import os
import random
import shutil
import traceback
import asyncio
import base64
import io
import re
from collections import defaultdict
from urllib.parse import parse_qs, urlparse

import aiohttp
import discord
import aiofiles
import requests
import openai
import httpx
from PIL import Image
from services.discord_attachment_service import save_attachment
from functools import partial
from typing import List, Optional
from pathlib import Path
from datetime import date

from discord import Interaction
from discord.ext import pages
from langchain_classic.memory import ConversationSummaryBufferMemory
from langchain_core.prompts import MessagesPlaceholder
from langchain_core.messages import SystemMessage
from langchain_core.tools import Tool
from llama_index.core.callbacks import CallbackManager, TokenCountingHandler
from llama_index.core.response_synthesizers import ResponseMode
from llama_index.core.indices.query.query_transform import StepDecomposeQueryTransform
from llama_index.core.prompts.chat_prompts import CHAT_REFINE_PROMPT
from llama_index.core.schema import Document
from llama_index.core.node_parser import TokenTextSplitter
from llama_index.core.retrievers import VectorIndexRetriever, TreeSelectLeafRetriever
from llama_index.core.query_engine import RetrieverQueryEngine, MultiStepQueryEngine
from llama_index.core import (
    VectorStoreIndex as GPTVectorStoreIndex,
    TreeIndex as GPTTreeIndex,
    SimpleDirectoryReader,
    PromptTemplate as QuestionAnswerPrompt,
    MockEmbedding,
    StorageContext,
    load_index_from_storage,
    get_response_synthesizer,
    VectorStoreIndex,
)
from llama_index.core.llms import MockLLM
from services.indexing_service import (
    IndexContext,
    create_index_context,
    read_web_documents,
    read_pdf_documents,
    RecursiveWebReader,
    persist_index,
    context_for_stored_index,
    embedding_for_index,
    index_tokenizer,
)

from models.embed_statics_model import EmbedStatics
from models.openai_model import Models, OpenAIAPIError
from services.environment_service import EnvService
from services.agent_service import (
    create_chat_llm, create_chat_agent, agent_output_text, personal_key_fingerprint,
)
from utils.safe_ctx_respond import safe_ctx_respond
from utils.api_errors import openai_error_message

SHORT_TO_LONG_CACHE = {}
MAX_DEEP_COMPOSE_PRICE = EnvService.get_max_deep_compose_price()


def index_name(value):
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError("Index names must be a single file name without slashes.")
    return value


def index_error_message(error):
    if isinstance(error, OpenAIAPIError):
        return openai_error_message(error)
    if isinstance(error, openai.APIError):
        return "OpenAI could not complete the request. Check the configured key, model access and billing, then try again."
    return str(error)


def get_service_context_with_llm(llm):
    return create_index_context(llm=llm)


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
    index = index_storage[user_id].get_index_or_throw()
    service_context.embed_model = embedding_for_index(index, service_context)
    if isinstance(index, GPTTreeIndex):
        retriever = TreeSelectLeafRetriever(
            index=index,
            child_branch_factor=child_branch_factor,
            llm=service_context.llm,
        )
    else:
        retriever = VectorIndexRetriever(
            index=index,
            similarity_top_k=nodes,
            embed_model=embedding_for_index(index, service_context),
        )
    synthesizer = get_response_synthesizer(
        response_mode=response_mode,
        use_async=False,
        refine_template=CHAT_REFINE_PROMPT,
        **service_context.query_kwargs,
    )
    query_engine = RetrieverQueryEngine(
        retriever=retriever, response_synthesizer=synthesizer
    )
    if multistep:
        query_engine = MultiStepQueryEngine(
            query_engine=query_engine,
            query_transform=StepDecomposeQueryTransform(llm=service_context.llm),
            response_synthesizer=synthesizer,
            index_summary="Provides information about the indexed documents.",
        )
    return query_engine.query(query)


class IndexChatData:
    def __init__(
        self,
        llm,
        agent_chain,
        memory,
        thread_id,
        tools,
        agent_kwargs,
        llm_predictor,
        owner_id=None,
        key_fingerprint=None,
    ):
        self.llm = llm
        self.agent_chain = agent_chain
        self.memory = memory
        self.thread_id = thread_id
        self.tools = tools
        self.agent_kwargs = agent_kwargs
        self.llm_predictor = llm_predictor
        self.owner_id = owner_id
        self.key_fingerprint = key_fingerprint


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
        file_name = re.sub(r"[/\\]", "_", file_name)
        file = f"{date.today().month}_{date.today().day}_{file_name}"
        # If file is > 93 in length, cut it off to 93
        if len(file) > 93:
            file = file[:93]

        persist_index(
            index,
            persist_dir=EnvService.save_path()
            / "indexes"
            / f"{str(user_id)}"
            / f"{file}",
        )

    def reset_indexes(self, user_id):
        self.individual_indexes = []
        self.queryable_index = None

        for folder in (str(user_id), f"{user_id}_search"):
            path = EnvService.save_path() / "indexes" / folder
            if path.is_dir():
                shutil.rmtree(path)


class Index_handler:
    IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
    AUDIO_SUFFIXES = {
        ".flac", ".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".ogg", ".wav", ".webm"
    }
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
        "audio/flac": ".flac",
        "audio/mp4": ".m4a",
        "audio/webm": ".webm",
        "audio/wav": ".wav",
        "video/mpeg": ".mpeg",
        "video/mp4": ".mp4",
        "video/webm": ".webm",
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

    def __init__(self, bot, usage_service, model=None, recovery=None):
        self.bot = bot
        self.model = model
        self.recovery = recovery
        self.openai_key = EnvService.get_openai_token()
        self.index_storage = defaultdict(IndexData)
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
        try:
            original = Path(original_path)
            target = Path(rename_path)
            if (
                len(original.parts) != 3
                or original.parts[0] != "indexes"
                or original.parent != target.parent
            ):
                return False
            index_name(original.name)
            index_name(target.name)
            source = EnvService.find_shared_file(original_path)
            if source.parent != (EnvService.save_path() / original.parent).resolve():
                return False
            destination = source.with_name(target.name)
            if destination.exists():
                return False
            source.rename(destination)
            return True
        except Exception:
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

            # Recovery already supplies an authorized thread with its REST guild.
            await ctx.channel.edit(name="Closed-GPT", archived=True)
            return "Ended chat session."

        self.usage_service.update_usage_memory(ctx.guild.name, "index_chat_message", 1)

        agent_output = await asyncio.to_thread(
            partial(
                self.index_chat_chains[ctx.channel.id].agent_chain.invoke,
                {"input": message},
            ),
        )
        return agent_output_text(agent_output["output"])

    async def index_chat_file(self, message: discord.Message, file: discord.Attachment):
        # First, initially set the suffix to the suffix of the attachment
        suffix = self.get_file_suffix(file.content_type, file.filename) or None

        if not suffix:
            await message.reply(
                "The file you uploaded is unable to be indexed. It is in an unsupported file format"
            )
            return False, None

        try:
            self.check_media_size(suffix, getattr(file, "size", 0))
        except ValueError as error:
            await message.reply(str(error))
            return False, None

        async with aiofiles.tempfile.TemporaryDirectory() as temp_path:
            async with aiofiles.tempfile.NamedTemporaryFile(
                suffix=suffix, dir=temp_path, delete=False
            ) as temp_file:
                try:
                    await save_attachment(file, temp_file.name)

                    filename = file.filename

                    # Assert that the filename is < 100 characters, if it is greater, truncate to the first 100 characters and keep the original ending
                    if len(filename) > 100:
                        filename = filename[:100] + filename[-4:]

                    print("Indexing")
                    index = await self.index_uploaded_file(
                        Path(temp_file.name),
                        get_service_context_with_llm(
                            self.index_chat_chains[message.channel.id].llm
                        ),
                        suffix,
                        filename,
                    )
                    print("Done Indexing")
                    self.usage_service.update_usage_memory(
                        message.guild.name, "index_chat_file", 1
                    )

                    summary = await index.as_query_engine(
                        response_mode="tree_summarize",
                        **get_service_context_with_llm(
                            self.index_chat_chains[message.channel.id].llm
                        ).query_kwargs,
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

                    engine = self.get_query_engine(
                        index, self.index_chat_chains[message.channel.id].llm
                    )

                    # Get rid of all special characters in the filename
                    filename = "".join(
                        [c for c in filename if c.isalpha() or c.isdigit()]
                    ).rstrip()

                    tool = Tool(
                        name=f"{filename}-index",
                        func=lambda query: str(engine.query(query)),
                        description=f"Use this tool for data related to this summary: {summary}",
                    )

                    tools = self.index_chat_chains[message.channel.id].tools
                    tools.append(tool)

                    agent_chain = create_chat_agent(
                        self.index_chat_chains[message.channel.id].llm,
                        tools,
                        self.index_chat_chains[message.channel.id]
                        .agent_kwargs["system_message"]
                        .content,
                        memory=self.index_chat_chains[message.channel.id].memory,
                    )

                    index_chat_data = IndexChatData(
                        self.index_chat_chains[message.channel.id].llm,
                        agent_chain,
                        self.index_chat_chains[message.channel.id].memory,
                        message.channel.id,
                        tools,
                        self.index_chat_chains[message.channel.id].agent_kwargs,
                        self.index_chat_chains[message.channel.id].llm_predictor,
                        owner_id=self.index_chat_chains[message.channel.id].owner_id,
                        key_fingerprint=self.index_chat_chains[message.channel.id].key_fingerprint,
                    )

                    self.index_chat_chains[message.channel.id] = index_chat_data

                    return True, summary
                except Exception as e:
                    await message.reply(
                        "There was an error indexing your file: " + index_error_message(e)
                    )
                    traceback.print_exc()
                    return False, None

    async def start_index_chat(self, ctx, model, temperature, top_p, user_api_key=None):
        guild_id = getattr(ctx.guild, "id", None) or ctx.interaction.guild_id
        if self.recovery is not None and self.bot.get_guild(guild_id) is None:
            if not self.recovery.enabled_for(guild_id):
                await ctx.respond("Discord has not delivered this server's events. Enable scoped REST recovery before starting a data chat here.")
                return
            if not await self.recovery.prepare_context(ctx):
                return
        preparation_message = await ctx.channel.send(
            embed=EmbedStatics.get_index_chat_preparation_message()
        )
        llm = create_chat_llm(
            model,
            temperature,
            top_p,
            api_key=user_api_key or self.openai_key,
            max_retries=0 if self.recovery and self.recovery.enabled_for(guild_id) else 2,
        )
        llm_predictor = create_index_context(llm=llm).llm

        max_token_limit = min(16000, Models.get_max_tokens(model) // 4)

        memory = ConversationSummaryBufferMemory(
            memory_key="memory",
            return_messages=True,
            llm=llm,
            max_token_limit=max_token_limit,
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
                "Avoid using single word queries unless the request is very simple. You can query multiple times to break down complex requests and retrieve more information. When calling functions, no special characters are allowed in the function name, keep that in mind."
            ),
        }

        tools = [
            Tool(
                name="Dummy-Tool-Do-Not-Use",
                func=dummy_tool,
                description="This is a dummy tool that does nothing, do not ever mention this tool or use this tool.",
            )
        ]

        agent_chain = create_chat_agent(
            llm, tools, agent_kwargs["system_message"].content, memory=memory
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
        await safe_ctx_respond(ctx=ctx, content="Conversation started.")

        try:
            await preparation_message.delete()
        except:
            pass

        index_chat_data = IndexChatData(
            llm,
            agent_chain,
            memory,
            thread.id,
            tools,
            agent_kwargs,
            llm_predictor,
            owner_id=ctx.user.id,
            key_fingerprint=personal_key_fingerprint(user_api_key),
        )

        self.index_chat_chains[thread.id] = index_chat_data
        if self.recovery is not None:
            await self.recovery.watch_thread(thread, new=True)

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
                    title="Index Query Results",
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
        suffix = (suffix or Path(file_path).suffix).lower()
        if suffix in {
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".svg",
            ".webp",
            ".psd",
            ".mp3",
            ".wav",
            ".ogg",
            ".flac",
            ".mpeg",
            ".mp4",
            ".m4a",
            ".webm",
        }:
            raise ValueError(
                "Image, audio and video indexing is unavailable. Use native vision or "
                "/transcribe, then index the extracted text."
            )
        if suffix in {".doc", ".ppt", ".xls", ".exe", ".jar"}:
            raise ValueError(
                "This binary format cannot be indexed. Convert legacy Office files "
                "to DOCX, PPTX or XLSX, or upload extracted text."
            )
        if suffix and suffix == ".md":
            from llama_index.readers.file import MarkdownReader

            loader = MarkdownReader()
            document = loader.load_data(file_path)
        elif suffix and suffix == ".epub":
            from llama_index.readers.file import EpubReader

            epub_loader = EpubReader()
            document = epub_loader.load_data(file_path)
        else:
            document = SimpleDirectoryReader(input_files=[file_path]).load_data()
        index = GPTVectorStoreIndex.from_documents(
            document, **service_context.index_kwargs, use_async=False
        )
        return index

    async def index_uploaded_file(self, file_path, context, suffix, filename):
        """Extract supported media before embedding, using this request's API key."""
        if suffix not in self.IMAGE_SUFFIXES | self.AUDIO_SUFFIXES:
            return await asyncio.to_thread(self.index_file, file_path, context, suffix)
        if self.model is None:
            raise ValueError("Media indexing requires the bot's OpenAI client.")
        self.check_media_size(suffix, Path(file_path).stat().st_size)
        key = context.embed_model.api_key
        if suffix in self.IMAGE_SUFFIXES:
            model = context.llm.model
            if not Models.supports_vision(model):
                raise ValueError("Choose a vision-capable model to index an image.")

            def image_data():
                with Image.open(file_path) as image:
                    image.thumbnail((2048, 2048))
                    output = io.BytesIO()
                    image.convert("RGB").save(output, format="PNG")
                return base64.b64encode(output.getvalue()).decode("ascii")

            encoded = await asyncio.to_thread(image_data)
            result = await self.model.send_request(
                [
                    {
                        "type": "text",
                        "text": "Extract the readable text and describe the visible information in this image for document retrieval. Preserve names, numbers and layout relationships. Do not invent details.",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{encoded}"},
                    },
                ],
                0,
                model=model,
                max_tokens_override=4096,
                custom_api_key=key,
            )
            text = result["choices"][0]["message"]["content"]
            extraction = "image text and description (first frame)"
        else:
            media = discord.File(file_path, filename=filename)
            try:
                text = await self.model.send_transcription_request(
                    media, custom_api_key=key
                )
            finally:
                media.close()
            extraction = "speech transcription; video frames are not indexed"
        if not isinstance(text, str) or not text.strip():
            raise ValueError(
                "No readable text or speech was extracted from this media."
            )
        return await asyncio.to_thread(
            GPTVectorStoreIndex.from_documents,
            [
                Document(
                    text=text,
                    metadata={"file_name": filename, "extraction": extraction},
                )
            ],
            **context.index_kwargs,
            use_async=False,
        )

    @classmethod
    def check_media_size(cls, suffix, size):
        if suffix in cls.IMAGE_SUFFIXES | cls.AUDIO_SUFFIXES:
            limit_mb = 10 if suffix in cls.IMAGE_SUFFIXES else 25
            if size > limit_mb * 1024 * 1024:
                raise ValueError(
                    f"This media file exceeds the {limit_mb} MiB indexing limit."
                )

    @classmethod
    def media_description(cls, suffix):
        if suffix in cls.IMAGE_SUFFIXES:
            return "Extracting text and visible information with OpenAI vision (first frame only; 10 MiB limit)."
        if suffix in cls.AUDIO_SUFFIXES:
            return "Indexing speech with OpenAI transcription (25 MiB limit). Video frames are not indexed."
        return None

    def index_gdoc(self, doc_id, service_context) -> GPTVectorStoreIndex:
        url = f"https://docs.google.com/document/d/{doc_id}/export?format=txt"
        # Public text export avoids interactive OAuth on a headless Discord bot.
        with requests.get(url, timeout=20, stream=True) as response:
            if (
                response.status_code != 200
                or "text/plain" not in response.headers.get("Content-Type", "").lower()
            ):
                raise ValueError(
                    "Google Docs must be shared for anyone with the link to view. For private documents, upload an exported TXT, PDF or DOCX file."
                )
            data = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                data.extend(chunk)
                if len(data) > 10 * 1024 * 1024:
                    raise ValueError(
                        "The Google Doc exceeds the 10 MiB text indexing limit."
                    )
        text = data.decode("utf-8-sig")
        if not text.strip():
            raise ValueError("The Google Doc contains no readable text.")
        document = [
            Document(
                text=text,
                metadata={"URL": f"https://docs.google.com/document/d/{doc_id}"},
            )
        ]
        index = GPTVectorStoreIndex.from_documents(
            document, **service_context.index_kwargs, use_async=False
        )
        return index

    def index_youtube_transcript(self, link, service_context):
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
        except ImportError as exc:
            raise ValueError("YouTube indexing requires youtube-transcript-api.") from exc
        canonical_link = self.youtube_link(link)
        video_id = parse_qs(urlparse(canonical_link).query)["v"][0]

        class TranscriptSession(requests.Session):
            def request(self, *args, **kwargs):
                kwargs.setdefault("timeout", 15)
                return super().request(*args, **kwargs)

        try:
            with TranscriptSession() as session:
                transcript = YouTubeTranscriptApi(http_client=session).fetch(video_id)
        except Exception as exc:
            raise ValueError("The YouTube transcript is unavailable. The video must have an accessible English transcript.") from exc
        text = "\n".join(chunk.text for chunk in transcript)
        if not text.strip():
            raise ValueError("The YouTube transcript contains no text.")
        documents = [Document(text=text, metadata={"URL": canonical_link, "video_id": video_id})]

        index = GPTVectorStoreIndex.from_documents(
            documents,
            **service_context.index_kwargs,
            use_async=False,
        )
        return index

    @staticmethod
    def youtube_link(link):
        parsed = urlparse(link)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        parts = parsed.path.strip("/").split("/")
        if host == "youtu.be":
            video_id = parts[0]
        elif host in {
            "youtube.com",
            "m.youtube.com",
            "music.youtube.com",
            "youtube-nocookie.com",
        }:
            if parts[0] == "watch":
                video_id = parse_qs(parsed.query).get("v", [""])[0]
            elif len(parts) == 2 and parts[0] in {"embed", "shorts", "live"}:
                video_id = parts[1]
            else:
                video_id = ""
        else:
            video_id = ""
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise ValueError(
                "Use a YouTube video link, not a channel or playlist link."
            )
        return f"https://www.youtube.com/watch?v={video_id}"

    def index_github_repository(self, link, service_context):
        parts = urlparse(link).path.strip("/").split("/")
        if len(parts) != 2 or not all(re.fullmatch(r"[\w.-]+", part) for part in parts):
            raise ValueError(
                "Use a repository URL such as https://github.com/owner/repository."
            )
        owner, repo = parts
        repo = repo.removesuffix(".git")

        try:
            from llama_index.readers.github import GithubClient, GithubRepositoryReader
        except ImportError as exc:
            raise ValueError(
                "GitHub indexing requires llama-index-readers-github."
            ) from exc
        token = EnvService.get_github_token()
        if not token:
            raise ValueError("GitHub repository indexing requires GITHUB_TOKEN.")
        response = requests.get(
            f"https://api.github.com/repos/{owner}/{repo}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=15,
        )
        if response.status_code != 200:
            raise ValueError(
                "The GitHub repository is unavailable to the configured GitHub token."
            )
        branch = response.json().get("default_branch")
        if not branch:
            raise ValueError("The GitHub repository has no default branch.")
        class ProxyGithubClient(GithubClient):
            async def request(self, endpoint, method, headers=None, timeout=5, retries=0, **kwargs):
                # The reader's custom transport disables HTTPX's environment proxy discovery.
                async with httpx.AsyncClient(
                    base_url=self._base_url,
                    proxy=EnvService.get_http_proxy(self._base_url),
                    headers={**await self._get_auth_headers(), **(headers or {})},
                    timeout=timeout,
                    transport=httpx.AsyncHTTPTransport(retries=retries),
                ) as client:
                    return await client.request(
                        method, self._endpoints[endpoint].format(**kwargs),
                        params=kwargs.get("params"),
                    )

        reader = GithubRepositoryReader(
            ProxyGithubClient(github_token=token),
            owner=owner,
            repo=repo,
        )
        documents = reader.load_data(branch=branch)

        index = GPTVectorStoreIndex.from_documents(
            documents,
            **service_context.index_kwargs,
            use_async=False,
        )
        return index

    def index_load_file(self, file_path, context=None):
        context = context or create_index_context(api_key=self.openai_key)
        storage_context = StorageContext.from_defaults(persist_dir=file_path)
        context = context_for_stored_index(file_path, context)
        index = load_index_from_storage(
            storage_context, embed_model=context.embed_model
        )
        return index

    def index_discord(self, document, service_context) -> GPTVectorStoreIndex:
        index = GPTVectorStoreIndex.from_documents(
            document,
            **service_context.index_kwargs,
            use_async=False,
        )
        return index

    async def index_pdf(self, url) -> list[Document]:
        return await read_pdf_documents(url)

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
                        if (
                            "application/pdf"
                            in response.headers.get("Content-Type", "").lower()
                        ):
                            documents = await self.index_pdf(url)
                            index = await asyncio.to_thread(
                                functools.partial(
                                    GPTVectorStoreIndex.from_documents,
                                    documents=documents,
                                    **service_context.index_kwargs,
                                    use_async=False,
                                ),
                            )

                            return index
        except:
            traceback.print_exc()
            raise ValueError("Could not load webpage")

        documents = await asyncio.to_thread(read_web_documents, url)

        index = await asyncio.to_thread(
            functools.partial(
                GPTVectorStoreIndex.from_documents,
                documents=documents,
                **service_context.index_kwargs,
                use_async=False,
            ),
        )
        return index

    def reset_indexes(self, user_id):
        self.index_storage[user_id].reset_indexes(user_id)

    def get_file_suffix(self, content_type, filename):
        if content_type:
            for key, value in self.type_to_suffix_mappings.items():
                if key in content_type.lower():
                    return value
        suffix = Path(filename).suffix.lower()
        if suffix in set(self.type_to_suffix_mappings.values()) | self.IMAGE_SUFFIXES | self.AUDIO_SUFFIXES:
            return suffix
        return None

    async def set_file_index(
        self, ctx: discord.ApplicationContext, file: discord.Attachment, user_api_key
    ):
        service_context_no_llm = create_index_context(
            api_key=user_api_key or self.openai_key
        )
        token_counter = service_context_no_llm.token_counter

        try:
            # First, initially set the suffix to the suffix of the attachment
            suffix = self.get_file_suffix(file.content_type, file.filename) or None

            if not suffix:
                await ctx.respond(
                    embed=EmbedStatics.get_index_set_failure_embed("Unsupported file")
                )
                return

            self.check_media_size(suffix, getattr(file, "size", 0))

            # Send indexing message
            progress_embed = EmbedStatics.build_index_progress_embed()
            media_description = self.media_description(suffix)
            if media_description:
                progress_embed.description = media_description
            response = await ctx.respond(embed=progress_embed)

            async with aiofiles.tempfile.TemporaryDirectory() as temp_path:
                async with aiofiles.tempfile.NamedTemporaryFile(
                    suffix=suffix, dir=temp_path, delete=False
                ) as temp_file:
                    await save_attachment(file, temp_file.name)
                    index = await self.index_uploaded_file(
                        Path(temp_file.name), service_context_no_llm, suffix, file.filename
                    )
                    await self.usage_service.update_usage(
                        token_counter.total_embedding_token_count,
                        service_context_no_llm.embed_model.model_name,
                    )

            try:
                price = await self.usage_service.get_price(
                    token_counter.total_embedding_token_count,
                    service_context_no_llm.embed_model.model_name,
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
                embed=EmbedStatics.get_index_set_failure_embed(index_error_message(e))
            )
            traceback.print_exc()

    async def set_link_index_recurse(
        self, ctx: discord.ApplicationContext, link: str, depth, user_api_key
    ):
        service_context_no_llm = create_index_context(
            api_key=user_api_key or self.openai_key
        )
        token_counter = service_context_no_llm.token_counter

        response = await ctx.respond(embed=EmbedStatics.build_index_progress_embed())
        try:
            # Pre-emptively connect and get the content-type of the response
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(link, timeout=2) as _response:
                        print(_response.status)
                        if _response.status != 200:
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
                        + index_error_message(e)
                    )
                )
                return

            # Check if the link contains youtube in it
            loader = RecursiveWebReader(depth=depth)
            documents = await asyncio.to_thread(
                partial(loader.load_data, [link])
            )
            index = await asyncio.to_thread(
                functools.partial(
                    GPTVectorStoreIndex.from_documents,
                    documents=documents,
                    **service_context_no_llm.index_kwargs,
                    use_async=False,
                ),
            )

            await self.usage_service.update_usage(
                token_counter.total_embedding_token_count,
                service_context_no_llm.embed_model.model_name,
            )

            try:
                price = await self.usage_service.get_price(
                    token_counter.total_embedding_token_count,
                    service_context_no_llm.embed_model.model_name,
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
            await response.edit(embed=EmbedStatics.get_index_set_failure_embed(index_error_message(e)))
            traceback.print_exc()
            return

        except Exception as e:
            await response.edit(embed=EmbedStatics.get_index_set_failure_embed(index_error_message(e)))
            traceback.print_exc()
            return

        await response.edit(embed=EmbedStatics.get_index_set_success_embed(price))

    def get_query_engine(self, index, llm):
        context = get_service_context_with_llm(llm)
        retriever = VectorIndexRetriever(
            index=index,
            similarity_top_k=6,
            embed_model=embedding_for_index(index, context),
        )
        synthesizer = get_response_synthesizer(
            response_mode=ResponseMode.COMPACT_ACCUMULATE,
            refine_template=CHAT_REFINE_PROMPT,
            **context.query_kwargs,
        )
        return RetrieverQueryEngine(
            retriever=retriever, response_synthesizer=synthesizer
        )

    async def index_link(
        self, link, summarize=False, index_chat_ctx=None, context=None
    ):
        if context is not None:
            service_context_no_llm = context
        elif index_chat_ctx is not None:
            service_context_no_llm = create_index_context(
                llm=self.index_chat_chains[index_chat_ctx.channel.id].llm
            )
        else:
            service_context_no_llm = create_index_context(api_key=self.openai_key)
        try:
            parsed = urlparse(link)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("Provide a complete HTTP or HTTPS link.")
            host = parsed.hostname.lower().removeprefix("www.")
            if host in {"youtube.com", "m.youtube.com", "music.youtube.com", "youtube-nocookie.com", "youtu.be"}:
                print("Indexing youtube transcript")
                index = await asyncio.to_thread(
                    partial(
                        self.index_youtube_transcript, link, service_context_no_llm
                    ),
                )
                print("Indexed youtube transcript")
            elif host == "github.com":
                index = await asyncio.to_thread(
                    partial(self.index_github_repository, link, service_context_no_llm),
                )
            elif host == "docs.google.com":
                match = re.fullmatch(r"/document/d/([A-Za-z0-9_-]+)(?:/.*)?", parsed.path)
                if not match:
                    raise ValueError("Use a Google Docs document link, or upload an exported file.")
                index = await asyncio.to_thread(
                    self.index_gdoc, match.group(1), service_context_no_llm
                )
            else:
                index = await self.index_webpage(link, service_context_no_llm)
        except Exception as e:
            if index_chat_ctx:
                await index_chat_ctx.reply(
                    "There was an error indexing your link: " + index_error_message(e)
                )
                return False, None
            else:
                raise e

        summary = None
        if index_chat_ctx:
            try:
                print("Getting transcript summary")
                self.usage_service.update_usage_memory(
                    index_chat_ctx.guild.name, "index_chat_link", 1
                )
                summary = await index.as_query_engine(
                    response_mode="tree_summarize",
                    **get_service_context_with_llm(
                        self.index_chat_chains[index_chat_ctx.channel.id].llm
                    ).query_kwargs,
                ).aquery(
                    "What is a summary or general idea of this document? Be detailed in your summary but not too verbose. Your summary should be under 50 words. This summary will be used in a vector index to retrieve information about certain data. So, at a high level, the summary should describe the document in such a way that a retriever would know to select it when asked questions about it. The link was {link}. Include the an easy identifier derived from the link at the end of the summary."
                )
                print("Got transcript summary")

                engine = self.get_query_engine(
                    index, self.index_chat_chains[index_chat_ctx.channel.id].llm
                )

                # Get rid of all special characters in the link, replace periods with _
                link_cleaned = "".join(
                    [c for c in link if c.isalpha() or c.isdigit() or c == "."]
                ).rstrip()
                # replace .
                link_cleaned = link_cleaned.replace(".", "_")

                # Shorten the link to the first 100 characters
                link_cleaned = link_cleaned[:50]

                tool = Tool(
                    name=f"{link_cleaned}-index",
                    func=lambda query: str(engine.query(query)),
                    description=f"Use this tool for data related to this summary: {summary}",
                )

                tools = self.index_chat_chains[index_chat_ctx.channel.id].tools
                tools.append(tool)

                agent_chain = create_chat_agent(
                    self.index_chat_chains[index_chat_ctx.channel.id].llm,
                    tools,
                    self.index_chat_chains[index_chat_ctx.channel.id]
                    .agent_kwargs["system_message"]
                    .content,
                    memory=self.index_chat_chains[index_chat_ctx.channel.id].memory,
                )

                index_chat_data = IndexChatData(
                    self.index_chat_chains[index_chat_ctx.channel.id].llm,
                    agent_chain,
                    self.index_chat_chains[index_chat_ctx.channel.id].memory,
                    index_chat_ctx.channel.id,
                    tools,
                    self.index_chat_chains[index_chat_ctx.channel.id].agent_kwargs,
                    self.index_chat_chains[index_chat_ctx.channel.id].llm_predictor,
                    owner_id=self.index_chat_chains[index_chat_ctx.channel.id].owner_id,
                    key_fingerprint=self.index_chat_chains[index_chat_ctx.channel.id].key_fingerprint,
                )

                self.index_chat_chains[index_chat_ctx.channel.id] = index_chat_data

                return True, summary
            except Exception as e:
                await index_chat_ctx.reply(
                    "There was an error indexing your link: " + index_error_message(e)
                )
                return False, None

        return index, summary

    async def set_link_index(
        self, ctx: discord.ApplicationContext, link: str, user_api_key
    ):
        service_context_no_llm = create_index_context(
            api_key=user_api_key or self.openai_key
        )
        token_counter = service_context_no_llm.token_counter

        response = await ctx.respond(embed=EmbedStatics.build_index_progress_embed())
        try:
            # Check if the link contains youtube in it
            index, _ = await self.index_link(link, context=service_context_no_llm)

            await self.usage_service.update_usage(
                token_counter.total_embedding_token_count,
                service_context_no_llm.embed_model.model_name,
            )

            try:
                price = await self.usage_service.get_price(
                    token_counter.total_embedding_token_count,
                    service_context_no_llm.embed_model.model_name,
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
            await response.edit(embed=EmbedStatics.get_index_set_failure_embed(index_error_message(e)))
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
        service_context_no_llm = create_index_context(
            api_key=user_api_key or self.openai_key
        )
        token_counter = service_context_no_llm.token_counter

        try:
            document = await self.load_data(
                channel_ids=[channel.id], limit=message_limit, oldest_first=False
            )
            index = await asyncio.to_thread(
                partial(self.index_discord, document, service_context_no_llm)
            )
            try:
                price = await self.usage_service.get_price(
                    token_counter.total_embedding_token_count,
                    service_context_no_llm.embed_model.model_name,
                )
            except Exception:
                traceback.print_exc()
                price = "Unknown"
            await self.usage_service.update_usage(
                token_counter.total_embedding_token_count,
                service_context_no_llm.embed_model.model_name,
            )
            self.index_storage[ctx.user.id].add_index(index, ctx.user.id, channel.name)
            await ctx.respond(embed=EmbedStatics.get_index_set_success_embed(price))
        except Exception as e:
            await ctx.respond(embed=EmbedStatics.get_index_set_failure_embed(index_error_message(e)))
            traceback.print_exc()

    async def load_index(
        self, ctx: discord.ApplicationContext, index, server, search, user_api_key
    ):
        service_context_no_llm = create_index_context(
            api_key=user_api_key or self.openai_key
        )

        try:
            index_name(index)
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
            index = await asyncio.to_thread(
                partial(self.index_load_file, index_file, service_context_no_llm)
            )
            self.index_storage[ctx.user.id].queryable_index = index
            await ctx.respond(embed=EmbedStatics.get_index_load_success_embed())
        except Exception as e:
            traceback.print_exc()
            await ctx.respond(embed=EmbedStatics.get_index_load_failure_embed(index_error_message(e)))

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

    async def compose_indexes(self, user_id, indexes, name, deep_compose, context=None):
        if name:
            index_name(name)
        for selected_index in indexes:
            index_name(selected_index)
        service_context_no_llm = context or create_index_context(
            api_key=self.openai_key
        )
        service_context = service_context_no_llm
        token_counter = service_context_no_llm.token_counter
        # Load all the indexes first
        index_objects = []
        for _index in indexes:
            try:
                index_file = EnvService.find_shared_file(f"indexes/{user_id}/{_index}")
            except ValueError:
                index_file = EnvService.find_shared_file(
                    f"indexes/{user_id}_search/{_index}"
                )

            index = await asyncio.to_thread(
                partial(self.index_load_file, index_file, service_context_no_llm)
            )
            index_objects.append(index)

        # For each index object, add its documents to a GPTTreeIndex
        if deep_compose:
            documents = []
            for _index in index_objects:
                documents.extend(await self.index_to_docs(_index, 256, 20))

            token_counter_mock = TokenCountingHandler(
                tokenizer=index_tokenizer().encode,
            )
            callbacks_mock = CallbackManager([token_counter_mock])
            service_context_mock = IndexContext(
                llm=MockLLM(callback_manager=callbacks_mock),
                embed_model=MockEmbedding(
                    embed_dim=1536, callback_manager=callbacks_mock
                ),
                callback_manager=callbacks_mock,
                token_counter=token_counter_mock,
            )

            # Run the mock call first
            await asyncio.to_thread(
                partial(
                    GPTTreeIndex.from_documents,
                    documents=documents,
                    **service_context_mock.index_kwargs,
                ),
            )
            total_usage_price = await self.usage_service.get_price(
                token_counter_mock.total_llm_token_count,
                service_context_no_llm.llm.model,
            ) + await self.usage_service.get_price(
                token_counter_mock.total_embedding_token_count,
                service_context_no_llm.embed_model.model_name,
            )
            print("The total composition price is: ", total_usage_price)
            if total_usage_price > MAX_DEEP_COMPOSE_PRICE:
                raise ValueError(
                    "Doing this deep search would be prohibitively expensive. Please try a narrower search scope."
                )

            tree_index = await asyncio.to_thread(
                partial(
                    GPTTreeIndex.from_documents,
                    documents=documents,
                    **service_context.index_kwargs,
                    use_async=False,
                ),
            )

            await self.usage_service.update_usage(
                token_counter.total_llm_token_count, service_context_no_llm.llm.model
            )
            await self.usage_service.update_usage(
                token_counter.total_embedding_token_count,
                service_context_no_llm.embed_model.model_name,
            )

            # Now we have a list of tree indexes, we can compose them
            if not name:
                name = f"{date.today().month}_{date.today().day}_composed_deep_index"

            # Save the composed index
            persist_index(
                tree_index,
                persist_dir=EnvService.save_path() / "indexes" / str(user_id) / name,
            )

            self.index_storage[user_id].queryable_index = tree_index

            return total_usage_price
        else:
            documents = []
            for _index in index_objects:
                documents.extend(await self.index_to_docs(_index))

            simple_index = await asyncio.to_thread(
                partial(
                    GPTVectorStoreIndex.from_documents,
                    documents=documents,
                    **service_context_no_llm.index_kwargs,
                    use_async=False,
                ),
            )

            await self.usage_service.update_usage(
                token_counter.total_embedding_token_count,
                service_context_no_llm.embed_model.model_name,
            )

            if not name:
                name = f"{date.today().month}_{date.today().day}_composed_index"

            # Save the composed index
            persist_index(
                simple_index,
                persist_dir=EnvService.save_path() / "indexes" / str(user_id) / name,
            )
            self.index_storage[user_id].queryable_index = simple_index

            try:
                price = await self.usage_service.get_price(
                    token_counter.total_embedding_token_count,
                    service_context_no_llm.embed_model.model_name,
                )
            except:
                price = "Unknown"

            return price

    async def backup_discord(
        self, ctx: discord.ApplicationContext, user_api_key, message_limit
    ):
        service_context_no_llm = create_index_context(
            api_key=user_api_key or self.openai_key
        )
        token_counter = service_context_no_llm.token_counter

        try:
            channel_ids: List[int] = []
            for c in ctx.guild.text_channels:
                channel_ids.append(c.id)
            document = await self.load_data(
                channel_ids=channel_ids, limit=message_limit, oldest_first=False
            )

            index = await asyncio.to_thread(
                partial(self.index_discord, document, service_context_no_llm)
            )
            await self.usage_service.update_usage(
                token_counter.total_embedding_token_count,
                service_context_no_llm.embed_model.model_name,
            )
            try:
                price = await self.usage_service.get_price(
                    token_counter.total_embedding_token_count,
                    service_context_no_llm.embed_model.model_name,
                )
            except Exception:
                traceback.print_exc()
                price = "Unknown"
            Path(EnvService.save_path() / "indexes" / str(ctx.guild.id)).mkdir(
                parents=True, exist_ok=True
            )
            persist_index(
                index,
                persist_dir=EnvService.save_path()
                / "indexes"
                / str(ctx.guild.id)
                / f"{ctx.guild.name.replace(' ', '-')}_{date.today().month}_{date.today().day}",
            )

            await ctx.respond(embed=EmbedStatics.get_index_set_success_embed(price))
        except Exception as e:
            await ctx.respond(embed=EmbedStatics.get_index_set_failure_embed((index_error_message(e))))
            traceback.print_exc()

    async def query(
        self,
        ctx: discord.ApplicationContext,
        query: str,
        response_mode,
        nodes,
        user_api_key,
        child_branch_factor,
        model=Models.DEFAULT,
        multistep=False,
    ):
        service_context_no_llm = create_index_context(
            api_key=user_api_key or self.openai_key, model=model
        )
        token_counter = service_context_no_llm.token_counter

        ctx_response = await ctx.respond(
            embed=EmbedStatics.build_index_query_progress_embed(query)
        )

        try:
            token_counter.reset_counts()
            response = await asyncio.to_thread(
                partial(
                    get_and_query,
                    ctx.user.id,
                    self.index_storage,
                    query,
                    response_mode,
                    nodes,
                    child_branch_factor,
                    service_context=service_context_no_llm,
                    multistep=multistep,
                ),
            )
            print("The last token usage was ", token_counter.total_llm_token_count)
            await self.usage_service.update_usage(
                token_counter.total_llm_token_count,
                await self.usage_service.get_cost_name(model),
            )
            await self.usage_service.update_usage(
                token_counter.total_embedding_token_count,
                service_context_no_llm.embed_model.model_name,
            )

            try:
                total_price = round(
                    await self.usage_service.get_price(
                        token_counter.total_llm_token_count,
                        await self.usage_service.get_cost_name(model),
                    )
                    + await self.usage_service.get_price(
                        token_counter.total_embedding_token_count,
                        service_context_no_llm.embed_model.model_name,
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
            print("Encountered error: " + index_error_message(e))

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
            channel_content, channel_name = await self.read_channel(
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
        service_context_no_llm = create_index_context(
            api_key=user_api_key or self.openai_key
        )

        if not self.index_storage[ctx.user.id].has_indexes(ctx.user.id):
            await ctx.respond(
                embed=EmbedStatics.get_index_compose_failure_embed(
                    "You must have at least one index to compose."
                )
            )
            return

        await ctx.respond(
            "Select the index(es) to compose. You can compose multiple indexes together, you can also Deep Compose a single index.",
            view=ComposeModal(self, ctx.user.id, name, context=service_context_no_llm),
            ephemeral=True,
        )


class ComposeModal(discord.ui.View):
    def __init__(self, index_cog, user_id, name=None, deep=None, context=None) -> None:
        super().__init__()
        # Get the argument named "user_key_db" and save it as USER_KEY_DB
        self.index_cog = index_cog
        self.user_id = user_id
        self.deep = deep
        self.context = context

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
                        (
                            False
                            if not self.deep_select.values
                            or self.deep_select.values[0] == "no"
                            else True
                        ),
                        context=self.context,
                    )
                except ValueError as e:
                    await interaction.followup.send(
                        index_error_message(e), ephemeral=True, delete_after=180
                    )
                    return False
                except Exception as e:
                    traceback.print_exc()
                    await interaction.followup.send(
                        embed=EmbedStatics.get_index_compose_failure_embed(
                            "An error occurred while composing the indexes: " + index_error_message(e)
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
                        "Your composed index is ready! You can load it with /index load now in the server."
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
