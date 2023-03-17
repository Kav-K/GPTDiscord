import functools
import os
import random
import tempfile
import traceback
import asyncio
import json
from collections import defaultdict

import aiohttp
import discord
import aiofiles
from functools import partial
from typing import List, Optional
from pathlib import Path
from datetime import date

from discord import InteractionResponse, Interaction
from discord.ext import pages
from langchain.llms import OpenAIChat
from llama_index.langchain_helpers.chatgpt import ChatGPTLLMPredictor
from langchain import OpenAI
from llama_index.optimization.optimizer import SentenceEmbeddingOptimizer
from llama_index.prompts.chat_prompts import CHAT_REFINE_PROMPT

from llama_index.readers import YoutubeTranscriptReader
from llama_index.readers.schema.base import Document
from llama_index.langchain_helpers.text_splitter import TokenTextSplitter

from llama_index import (
    GPTSimpleVectorIndex,
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
)
from llama_index.readers.web import DEFAULT_WEBSITE_EXTRACTOR

from llama_index.composability import ComposableGraph

from models.embed_statics_model import EmbedStatics
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
    llm_predictor,
    embed_model,
    child_branch_factor,
):
    index: [GPTSimpleVectorIndex, ComposableGraph] = index_storage[
        user_id
    ].get_index_or_throw()
    if isinstance(index, GPTTreeIndex):
        response = index.query(
            query,
            child_branch_factor=child_branch_factor,
            llm_predictor=llm_predictor,
            refine_template=CHAT_REFINE_PROMPT,
            embed_model=embed_model,
            use_async=True,
            # optimizer=SentenceEmbeddingOptimizer(threshold_cutoff=0.7)
        )
    else:
        response = index.query(
            query,
            response_mode=response_mode,
            llm_predictor=llm_predictor,
            embed_model=embed_model,
            similarity_top_k=nodes,
            refine_template=CHAT_REFINE_PROMPT,
            use_async=True,
            # optimizer=SentenceEmbeddingOptimizer(threshold_cutoff=0.7)
        )
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
        file = f"{file_name}_{date.today().month}_{date.today().day}"
        # If file is > 93 in length, cut it off to 93
        if len(file) > 93:
            file = file[:93]

        index.save_to_disk(
            EnvService.save_path() / "indexes" / f"{str(user_id)}" / f"{file}.json"
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

    async def rename_index(self, ctx, original_path, rename_path):
        """Command handler to rename a user index"""

        index_file = EnvService.find_shared_file(original_path)
        if not index_file:
            return False

        # Rename the file at f"indexes/{ctx.user.id}/{user_index}" to f"indexes/{ctx.user.id}/{new_name}" using Pathlib
        try:
            if not rename_path.endswith(".json"):
                rename_path = rename_path + ".json"
            Path(original_path).rename(rename_path)
            return True
        except Exception as e:
            traceback.print_exc()
            return False

    async def paginate_embed(self, response_text):
        """Given a response text make embed pages and return a list of the pages. Codex makes it a codeblock in the embed"""

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

    def index_file(self, file_path, embed_model, suffix=None) -> GPTSimpleVectorIndex:
        if suffix and suffix == ".md":
            print("Loading a markdown file")
            loader = MarkdownReader()
            document = loader.load_data(file_path)
        elif suffix and suffix == ".epub":
            print("Loading an epub")
            epub_loader = EpubReader()
            print("The file path is ", file_path)
            document = epub_loader.load_data(file_path)
        else:
            document = SimpleDirectoryReader(input_files=[file_path]).load_data()
        index = GPTSimpleVectorIndex(document, embed_model=embed_model, use_async=True)
        return index

    def index_gdoc(self, doc_id, embed_model) -> GPTSimpleVectorIndex:
        document = GoogleDocsReader().load_data(doc_id)
        index = GPTSimpleVectorIndex(document, embed_model=embed_model, use_async=True)
        return index

    def index_youtube_transcript(self, link, embed_model):
        try:
            documents = YoutubeTranscriptReader().load_data(ytlinks=[link])
        except Exception as e:
            raise ValueError(f"The youtube transcript couldn't be loaded: {e}")

        index = GPTSimpleVectorIndex(
            documents,
            embed_model=embed_model,
            use_async=True,
        )
        return index

    def index_github_repository(self, link, embed_model):
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

        index = GPTSimpleVectorIndex(
            documents,
            embed_model=embed_model,
            use_async=True,
        )
        return index

    def index_load_file(self, file_path) -> [GPTSimpleVectorIndex, ComposableGraph]:
        with open(file_path, "r", encoding="utf8") as f:
            file_contents = f.read()
            index_dict = json.loads(file_contents)
            doc_id = index_dict["index_struct_id"]
            doc_type = index_dict["docstore"]["docs"][doc_id]["__type__"]
            f.close()
        if doc_type == "tree":
            index = GPTTreeIndex.load_from_disk(file_path)
        else:
            index = GPTSimpleVectorIndex.load_from_disk(file_path)
        return index

    def index_discord(self, document, embed_model) -> GPTSimpleVectorIndex:
        index = GPTSimpleVectorIndex(
            document,
            embed_model=embed_model,
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

    async def index_webpage(self, url, embed_model) -> GPTSimpleVectorIndex:
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
                                    GPTSimpleVectorIndex,
                                    documents=documents,
                                    embed_model=embed_model,
                                    use_async=True,
                                ),
                            )

                            return index
        except:
            raise ValueError("Could not load webpage")

        documents = BeautifulSoupWebReader(
            website_extractor=DEFAULT_WEBSITE_EXTRACTOR
        ).load_data(urls=[url])

        # index = GPTSimpleVectorIndex(documents, embed_model=embed_model, use_async=True)
        index = await self.loop.run_in_executor(
            None,
            functools.partial(
                GPTSimpleVectorIndex,
                documents=documents,
                embed_model=embed_model,
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
            print("The suffix is " + suffix)

            async with aiofiles.tempfile.TemporaryDirectory() as temp_path:
                async with aiofiles.tempfile.NamedTemporaryFile(
                    suffix=suffix, dir=temp_path, delete=False
                ) as temp_file:
                    await file.save(temp_file.name)
                    embedding_model = OpenAIEmbedding()
                    index = await self.loop.run_in_executor(
                        None,
                        partial(
                            self.index_file,
                            Path(temp_file.name),
                            embedding_model,
                            suffix,
                        ),
                    )
                    await self.usage_service.update_usage(
                        embedding_model.last_token_usage, embeddings=True
                    )

            try:
                price = await self.usage_service.get_price(
                    embedding_model.last_token_usage, embeddings=True
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

        response = await ctx.respond(embed=EmbedStatics.build_index_progress_embed())
        try:
            embedding_model = OpenAIEmbedding()

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
                    GPTSimpleVectorIndex,
                    documents=documents,
                    embed_model=embedding_model,
                    use_async=True,
                ),
            )

            await self.usage_service.update_usage(
                embedding_model.last_token_usage, embeddings=True
            )

            try:
                price = await self.usage_service.get_price(
                    embedding_model.last_token_usage, embeddings=True
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

        response = await ctx.respond(embed=EmbedStatics.build_index_progress_embed())
        try:
            embedding_model = OpenAIEmbedding()

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
            if "youtube" in link:
                index = await self.loop.run_in_executor(
                    None, partial(self.index_youtube_transcript, link, embedding_model)
                )
            elif "github" in link:
                index = await self.loop.run_in_executor(
                    None, partial(self.index_github_repository, link, embedding_model)
                )
            else:
                index = await self.index_webpage(link, embedding_model)
            await self.usage_service.update_usage(
                embedding_model.last_token_usage, embeddings=True
            )

            try:
                price = await self.usage_service.get_price(
                    embedding_model.last_token_usage, embeddings=True
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

        try:
            document = await self.load_data(
                channel_ids=[channel.id], limit=message_limit, oldest_first=False
            )
            embedding_model = OpenAIEmbedding()
            index = await self.loop.run_in_executor(
                None, partial(self.index_discord, document, embedding_model)
            )
            try:
                price = await self.usage_service.get_price(
                    embedding_model.last_token_usage, embeddings=True
                )
            except Exception:
                traceback.print_exc()
                price = "Unknown"
            await self.usage_service.update_usage(
                embedding_model.last_token_usage, embeddings=True
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
    ) -> List[Document]:
        documents = []
        for doc_id in old_index.docstore.docs.keys():
            text = ""
            if isinstance(old_index, GPTSimpleVectorIndex):
                nodes = old_index.docstore.get_document(doc_id).get_nodes(
                    old_index.docstore.docs[doc_id].id_map
                )
                for node in nodes:
                    extra_info = node.extra_info
                    text += f"{node.text} "
            if isinstance(old_index, GPTTreeIndex):
                nodes = old_index.docstore.get_document(doc_id).all_nodes.items()
                for node in nodes:
                    extra_info = node[1].extra_info
                    text += f"{node[1].text} "
            text_splitter = TokenTextSplitter(
                separator=" ", chunk_size=chunk_size, chunk_overlap=chunk_overlap
            )
            text_chunks = text_splitter.split_text(text)
            for text in text_chunks:
                document = Document(text, extra_info=extra_info)
                documents.append(document)
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
            llm=OpenAIChat(temperature=0, model_name="gpt-3.5-turbo")
        )

        # For each index object, add its documents to a GPTTreeIndex
        if deep_compose:
            documents = []
            for _index in index_objects:
                documents.extend(await self.index_to_docs(_index, 256, 20))

            embedding_model = OpenAIEmbedding()

            llm_predictor_mock = MockLLMPredictor(4096)
            embedding_model_mock = MockEmbedding(1536)

            # Run the mock call first
            await self.loop.run_in_executor(
                None,
                partial(
                    GPTTreeIndex,
                    documents=documents,
                    llm_predictor=llm_predictor_mock,
                    embed_model=embedding_model_mock,
                ),
            )
            total_usage_price = await self.usage_service.get_price(
                llm_predictor_mock.last_token_usage,
                chatgpt=True,  # TODO Enable again when tree indexes are fixed
            ) + await self.usage_service.get_price(
                embedding_model_mock.last_token_usage, embeddings=True
            )
            print("The total composition price is: ", total_usage_price)
            if total_usage_price > MAX_DEEP_COMPOSE_PRICE:
                raise ValueError(
                    "Doing this deep search would be prohibitively expensive. Please try a narrower search scope."
                )

            tree_index = await self.loop.run_in_executor(
                None,
                partial(
                    GPTTreeIndex,
                    documents=documents,
                    llm_predictor=llm_predictor,
                    embed_model=embedding_model,
                    use_async=True,
                ),
            )

            await self.usage_service.update_usage(
                llm_predictor.last_token_usage, chatgpt=True
            )
            await self.usage_service.update_usage(
                embedding_model.last_token_usage, embeddings=True
            )

            # Now we have a list of tree indexes, we can compose them
            if not name:
                name = (
                    f"composed_deep_index_{date.today().month}_{date.today().day}.json"
                )

            # Save the composed index
            tree_index.save_to_disk(
                EnvService.save_path() / "indexes" / str(user_id) / name
            )

            self.index_storage[user_id].queryable_index = tree_index

            return total_usage_price
        else:
            documents = []
            for _index in index_objects:
                documents.extend(await self.index_to_docs(_index))

            embedding_model = OpenAIEmbedding()

            simple_index = await self.loop.run_in_executor(
                None,
                partial(
                    GPTSimpleVectorIndex,
                    documents=documents,
                    embed_model=embedding_model,
                    use_async=True,
                ),
            )

            await self.usage_service.update_usage(
                embedding_model.last_token_usage, embeddings=True
            )

            if not name:
                name = f"composed_index_{date.today().month}_{date.today().day}.json"

            # Save the composed index
            simple_index.save_to_disk(
                EnvService.save_path() / "indexes" / str(user_id) / name
            )
            self.index_storage[user_id].queryable_index = simple_index

            try:
                price = await self.usage_service.get_price(
                    embedding_model.last_token_usage, embeddings=True
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

        try:
            channel_ids: List[int] = []
            for c in ctx.guild.text_channels:
                channel_ids.append(c.id)
            document = await self.load_data(
                channel_ids=channel_ids, limit=message_limit, oldest_first=False
            )
            embedding_model = OpenAIEmbedding()
            index = await self.loop.run_in_executor(
                None, partial(self.index_discord, document, embedding_model)
            )
            await self.usage_service.update_usage(
                embedding_model.last_token_usage, embeddings=True
            )
            try:
                price = await self.usage_service.get_price(
                    embedding_model.last_token_usage, embeddings=True
                )
            except Exception:
                traceback.print_exc()
                price = "Unknown"
            Path(EnvService.save_path() / "indexes" / str(ctx.guild.id)).mkdir(
                parents=True, exist_ok=True
            )
            index.save_to_disk(
                EnvService.save_path()
                / "indexes"
                / str(ctx.guild.id)
                / f"{ctx.guild.name.replace(' ', '-')}_{date.today().month}_{date.today().day}.json"
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
    ):
        if not user_api_key:
            os.environ["OPENAI_API_KEY"] = self.openai_key
        else:
            os.environ["OPENAI_API_KEY"] = user_api_key

        llm_predictor = LLMPredictor(
            llm=OpenAIChat(temperature=0, model_name="gpt-3.5-turbo")
        )

        ctx_response = await ctx.respond(
            embed=EmbedStatics.build_index_query_progress_embed(query)
        )

        try:
            embedding_model = OpenAIEmbedding()
            embedding_model.last_token_usage = 0
            response = await self.loop.run_in_executor(
                None,
                partial(
                    get_and_query,
                    ctx.user.id,
                    self.index_storage,
                    query,
                    response_mode,
                    nodes,
                    llm_predictor,
                    embedding_model,
                    child_branch_factor,
                ),
            )
            print("The last token usage was ", llm_predictor.last_token_usage)
            await self.usage_service.update_usage(
                llm_predictor.last_token_usage, chatgpt=True
            )
            await self.usage_service.update_usage(
                embedding_model.last_token_usage, embeddings=True
            )

            try:
                total_price = round(
                    await self.usage_service.get_price(
                        llm_predictor.last_token_usage, chatgpt=True
                    )
                    + await self.usage_service.get_price(
                        embedding_model.last_token_usage, embeddings=True
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
                ),
                delete_after=10,
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
