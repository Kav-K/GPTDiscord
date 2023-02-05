import os
import traceback
import asyncio
from collections import defaultdict

import discord
import aiofiles
from functools import partial
from typing import List, Optional
from pathlib import Path
from datetime import date
from langchain import OpenAI

from gpt_index.readers import YoutubeTranscriptReader
from gpt_index.readers.schema.base import Document

from gpt_index import (
    GPTSimpleVectorIndex,
    SimpleDirectoryReader,
    QuestionAnswerPrompt,
    BeautifulSoupWebReader,
    GPTListIndex,
    QueryMode,
    GPTTreeIndex,
    GoogleDocsReader,
    MockLLMPredictor,
    LLMPredictor,
    QueryConfig,
    PromptHelper,
    IndexStructType,
    OpenAIEmbedding,
)
from gpt_index.readers.web import DEFAULT_WEBSITE_EXTRACTOR

from gpt_index.composability import ComposableGraph

from services.environment_service import EnvService, app_root_path

SHORT_TO_LONG_CACHE = {}


def get_and_query(
    user_id, index_storage, query, response_mode, nodes, llm_predictor, embed_model
):
    index: [GPTSimpleVectorIndex, ComposableGraph] = index_storage[
        user_id
    ].get_index_or_throw()
    prompthelper = PromptHelper(4096, 500, 20)
    if isinstance(index, GPTTreeIndex):
        response = index.query(
            query,
            verbose=True,
            child_branch_factor=2,
            llm_predictor=llm_predictor,
            embed_model=embed_model,
            prompt_helper=prompthelper,
        )
    else:
        response = index.query(
            query,
            response_mode=response_mode,
            verbose=True,
            llm_predictor=llm_predictor,
            embed_model=embed_model,
            similarity_top_k=nodes,
            prompt_helper=prompthelper,
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
            return len(os.listdir(f"{app_root_path()}/indexes/{user_id}")) > 0
        except Exception:
            return False

    def add_index(self, index, user_id, file_name):
        self.individual_indexes.append(index)
        self.queryable_index = index

        # Create a folder called "indexes/{USER_ID}" if it doesn't exist already
        Path(f"{app_root_path()}/indexes/{user_id}").mkdir(parents=True, exist_ok=True)
        # Save the index to file under the user id
        index.save_to_disk(
            app_root_path()
            / "indexes"
            / f"{str(user_id)}"
            / f"{file_name}_{date.today().month}_{date.today().day}.json"
        )

    def reset_indexes(self, user_id):
        self.individual_indexes = []
        self.queryable_index = None

        # Delete the user indexes
        try:
            # First, clear all the files inside it
            for file in os.listdir(f"{app_root_path()}/indexes/{user_id}"):
                os.remove(f"{app_root_path()}/indexes/{user_id}/{file}")

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

    # TODO We need to do predictions below for token usage.
    def index_file(self, file_path, embed_model) -> GPTSimpleVectorIndex:
        document = SimpleDirectoryReader(file_path).load_data()
        index = GPTSimpleVectorIndex(document, embed_model=embed_model)
        return index

    def index_gdoc(self, doc_id, embed_model) -> GPTSimpleVectorIndex:
        document = GoogleDocsReader().load_data(doc_id)
        index = GPTSimpleVectorIndex(document, embed_model=embed_model)
        return index

    def index_youtube_transcript(self, link, embed_model):
        documents = YoutubeTranscriptReader().load_data(ytlinks=[link])
        index = GPTSimpleVectorIndex(
            documents, embed_model=embed_model,
        )
        return index

    def index_load_file(self, file_path) -> [GPTSimpleVectorIndex, ComposableGraph]:
        if "composed_deep" in str(file_path):
            index = GPTTreeIndex.load_from_disk(file_path)
        else:
            index = GPTSimpleVectorIndex.load_from_disk(file_path)
        return index

    def index_discord(self, document, embed_model) -> GPTSimpleVectorIndex:
        index = GPTSimpleVectorIndex(
            document, embed_model=embed_model,
        )
        return index

    def index_webpage(self, url, embed_model) -> GPTSimpleVectorIndex:
        documents = BeautifulSoupWebReader(
            website_extractor=DEFAULT_WEBSITE_EXTRACTOR
        ).load_data(urls=[url])
        index = GPTSimpleVectorIndex(documents, embed_model=embed_model)
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

        try:
            print(file.content_type)
            if file.content_type.startswith("text/plain"):
                suffix = ".txt"
            elif file.content_type.startswith("application/pdf"):
                suffix = ".pdf"
            # Allow for images too
            elif file.content_type.startswith("image/png"):
                suffix = ".png"
            elif file.content_type.startswith("image/"):
                suffix = ".jpg"
            elif "csv" in file.content_type:
                suffix = ".csv"
            elif "vnd." in file.content_type:
                suffix = ".pptx"
            # Catch all audio files and suffix with "mp3"
            elif file.content_type.startswith("audio/"):
                suffix = ".mp3"
            # Catch video files
            elif file.content_type.startswith("video/"):
                pass  # No suffix change
            else:
                await ctx.respond(
                    "Only accepts text, pdf, images, spreadheets, powerpoint, and audio/video files."
                )
                return
            async with aiofiles.tempfile.TemporaryDirectory() as temp_path:
                async with aiofiles.tempfile.NamedTemporaryFile(
                    suffix=suffix, dir=temp_path, delete=False
                ) as temp_file:
                    await file.save(temp_file.name)
                    embedding_model = OpenAIEmbedding()
                    index = await self.loop.run_in_executor(
                        None, partial(self.index_file, temp_path, embedding_model)
                    )
                    await self.usage_service.update_usage(embedding_model.last_token_usage, embeddings=True)

            file_name = file.filename
            self.index_storage[ctx.user.id].add_index(index, ctx.user.id, file_name)
            await ctx.respond("Index added to your indexes.")
        except Exception:
            await ctx.respond("Failed to set index")
            traceback.print_exc()

    async def set_link_index(
        self, ctx: discord.ApplicationContext, link: str, user_api_key
    ):
        if not user_api_key:
            os.environ["OPENAI_API_KEY"] = self.openai_key
        else:
            os.environ["OPENAI_API_KEY"] = user_api_key

        # TODO Link validation
        try:
            embedding_model = OpenAIEmbedding()
            # Check if the link contains youtube in it
            if "youtube" in link:
                index = await self.loop.run_in_executor(
                    None, partial(self.index_youtube_transcript, link, embedding_model)
                )
            else:
                index = await self.loop.run_in_executor(
                    None, partial(self.index_webpage, link, embedding_model)
                )
            await self.usage_service.update_usage(embedding_model.last_token_usage, embeddings=True)

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

        except Exception:
            await ctx.respond("Failed to set index")
            traceback.print_exc()

        await ctx.respond("Index set")

    async def set_discord_index(
        self,
        ctx: discord.ApplicationContext,
        channel: discord.TextChannel,
        user_api_key,
    ):
        if not user_api_key:
            os.environ["OPENAI_API_KEY"] = self.openai_key
        else:
            os.environ["OPENAI_API_KEY"] = user_api_key

        try:
            document = await self.load_data(
                channel_ids=[channel.id], limit=1000, oldest_first=False
            )
            embedding_model = OpenAIEmbedding()
            index = await self.loop.run_in_executor(
                None, partial(self.index_discord, document, embedding_model)
            )
            await self.usage_service.update_usage(embedding_model.last_token_usage, embeddings=True)
            self.index_storage[ctx.user.id].add_index(index, ctx.user.id, channel.name)
            await ctx.respond("Index set")
        except Exception:
            await ctx.respond("Failed to set index")
            traceback.print_exc()

    async def load_index(
        self, ctx: discord.ApplicationContext, index, server, user_api_key
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
            else:
                index_file = EnvService.find_shared_file(
                    f"indexes/{ctx.user.id}/{index}"
                )
            index = await self.loop.run_in_executor(
                None, partial(self.index_load_file, index_file)
            )
            self.index_storage[ctx.user.id].queryable_index = index
            await ctx.respond("Loaded index")
        except Exception as e:
            await ctx.respond(e)

    async def compose_indexes(self, user_id, indexes, name, deep_compose):
        # Load all the indexes first
        index_objects = []
        for _index in indexes:
            index_file = EnvService.find_shared_file(f"indexes/{user_id}/{_index}")
            index = await self.loop.run_in_executor(
                None, partial(self.index_load_file, index_file)
            )
            index_objects.append(index)

        # For each index object, add its documents to a GPTTreeIndex
        if deep_compose:
            documents = []
            for _index in index_objects:
                [
                    documents.append(_index.docstore.get_document(doc_id))
                    for doc_id in [docmeta for docmeta in _index.docstore.docs.keys()]
                    if isinstance(_index.docstore.get_document(doc_id), Document)
                ]
            llm_predictor = LLMPredictor(llm=OpenAI(model_name="text-davinci-003"))
            embedding_model = OpenAIEmbedding()
            tree_index = GPTTreeIndex(
                documents=documents,
                llm_predictor=llm_predictor,
                embed_model=embedding_model,
            )
            await self.usage_service.update_usage(
                llm_predictor.last_token_usage
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
            tree_index.save_to_disk(f"indexes/{user_id}/{name}.json")

            self.index_storage[user_id].queryable_index = tree_index
        else:
            documents = []
            for _index in index_objects:
                [
                    documents.append(_index.docstore.get_document(doc_id))
                    for doc_id in [docmeta for docmeta in _index.docstore.docs.keys()]
                    if isinstance(_index.docstore.get_document(doc_id), Document)
                ]

            embedding_model = OpenAIEmbedding()
            # Add everything into a simple vector index
            simple_index = GPTSimpleVectorIndex(
                documents=documents, embed_model=embedding_model
            )
            await self.usage_service.update_usage(embedding_model.last_token_usage, embeddings=True)

            if not name:
                name = f"composed_index_{date.today().month}_{date.today().day}.json"

            # Save the composed index
            simple_index.save_to_disk(f"indexes/{user_id}/{name}.json")
            self.index_storage[user_id].queryable_index = simple_index

    async def backup_discord(self, ctx: discord.ApplicationContext, user_api_key):
        if not user_api_key:
            os.environ["OPENAI_API_KEY"] = self.openai_key
        else:
            os.environ["OPENAI_API_KEY"] = user_api_key

        try:
            channel_ids: List[int] = []
            for c in ctx.guild.text_channels:
                channel_ids.append(c.id)
            document = await self.load_data(
                channel_ids=channel_ids, limit=3000, oldest_first=False
            )
            embedding_model = OpenAIEmbedding()
            index = await self.loop.run_in_executor(
                None, partial(self.index_discord, document, embedding_model)
            )
            await self.usage_service.update_usage(embedding_model.last_token_usage, embeddings=True)
            Path(app_root_path() / "indexes" / str(ctx.guild.id)).mkdir(
                parents=True, exist_ok=True
            )
            index.save_to_disk(
                app_root_path()
                / "indexes"
                / str(ctx.guild.id)
                / f"{ctx.guild.name.replace(' ', '-')}_{date.today().month}_{date.today().day}.json"
            )

            await ctx.respond("Backup saved")
        except Exception:
            await ctx.respond("Failed to save backup")
            traceback.print_exc()

    async def query(
        self,
        ctx: discord.ApplicationContext,
        query: str,
        response_mode,
        nodes,
        user_api_key,
    ):
        if not user_api_key:
            os.environ["OPENAI_API_KEY"] = self.openai_key
        else:
            os.environ["OPENAI_API_KEY"] = user_api_key

        try:
            llm_predictor = LLMPredictor(llm=OpenAI(model_name="text-davinci-003"))
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
                ),
            )
            print("The last token usage was ", llm_predictor.last_token_usage)
            await self.usage_service.update_usage(llm_predictor.last_token_usage)
            await self.usage_service.update_usage(embedding_model.last_token_usage, embeddings=True)
            await ctx.respond(
                f"**Query:**\n\n{query.strip()}\n\n**Query response:**\n\n{response.response.strip()}"
            )
        except Exception:
            traceback.print_exc()
            await ctx.respond(
                "Failed to send query. You may not have an index set, load an index with /index load",
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
            await ctx.respond("You must load at least one indexes before composing")
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

        # Map everything into the short to long cache
        for index in self.indexes:
            SHORT_TO_LONG_CACHE[index[:99]] = index

        # A text entry field for the name of the composed index
        self.name = name

        # A discord UI select menu with all the indexes. Limited to 25 entries. For the label field in the SelectOption,
        # cut it off at 100 characters to prevent the message from being too long

        self.index_select = discord.ui.Select(
            placeholder="Select index(es) to compose",
            options=[
                discord.SelectOption(label=str(index)[:99], value=index[:99])
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
                            discord.SelectOption(label=index[:99], value=index[:99])
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
                    "You must select at least 1 index", ephemeral=True
                )
            else:
                composing_message = await interaction.response.send_message(
                    "Composing indexes, this may take a long time...",
                    ephemeral=True,
                    delete_after=120,
                )
                # Compose the indexes
                await self.index_cog.compose_indexes(
                    self.user_id,
                    indexes,
                    self.name,
                    False
                    if not self.deep_select.values or self.deep_select.values[0] == "no"
                    else True,
                )
                await interaction.followup.send(
                    "Composed indexes", ephemeral=True, delete_after=10
                )

                try:
                    await composing_message.delete()
                except:
                    pass
        else:
            await interaction.response.defer(ephemeral=True)
