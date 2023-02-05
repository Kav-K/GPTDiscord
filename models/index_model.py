import os
import traceback
import asyncio
from collections import defaultdict

import discord
import aiofiles
from functools import partial
from typing import List, Optional
from pathlib import Path
from datetime import date, datetime

from gpt_index.readers.schema.base import Document
from gpt_index import GPTSimpleVectorIndex, SimpleDirectoryReader, QuestionAnswerPrompt, BeautifulSoupWebReader, \
    GPTFaissIndex, GPTListIndex, QueryMode, GPTTreeIndex
from gpt_index.readers.web import DEFAULT_WEBSITE_EXTRACTOR

from gpt_index.composability import ComposableGraph

from services.environment_service import EnvService, app_root_path



class IndexData:
    def __init__(self):
        self.queryable_index = None
        self.individual_indexes = []

    # A safety check for the future
    def get_index_or_throw(self):
        if not self.queryable():
            raise Exception("An index access was attempted before an index was created. This is a programmer error, please report this to the maintainers.")
        return self.queryable_index
    def queryable(self):
        return self.queryable_index is not None

    def add_index(self, index, user_id, file_name):
        self.individual_indexes.append(index)
        self.queryable_index = index

        # Create a folder called "indexes/{USER_ID}" if it doesn't exist already
        Path(f"{app_root_path()}/indexes/{user_id}").mkdir(parents=True, exist_ok=True)
        print(f"{app_root_path()}/indexes/{user_id}")
        # Save the index to file under the user id
        index.save_to_disk(app_root_path() / "indexes" / f"{str(user_id)}"/f"{file_name}_{date.today()}-H{datetime.now().hour}.json")

    def reset_indexes(self, user_id):
        self.individual_indexes = []
        self.queryable_index = None

        # Delete the user indexes
        try:
            # First, clear all the files inside it
            for file in os.listdir(f"{app_root_path()}/indexes/{user_id}"):
                os.remove(f"{app_root_path()}/indexes/{user_id}/{file}")

        except:
            traceback.print_exc()
            pass

class Index_handler:
    def __init__(self, bot):
        self.bot = bot
        self.openai_key = os.getenv("OPENAI_TOKEN")
        self.index_storage = defaultdict(IndexData)
        self.loop = asyncio.get_running_loop()
        self.qaprompt = QuestionAnswerPrompt(
            "Context information is below. The text '<|endofstatement|>' is used to separate chat entries and make it easier for you to understand the context\n"
            "---------------------\n"
            "{context_str}"
            "\n---------------------\n"
            "Never say '<|endofstatement|>'\n"
            "Given the context information and not prior knowledge, "
            "answer the question: {query_str}\n"
        )
    
    def index_file(self, file_path):
        document = SimpleDirectoryReader(file_path).load_data()
        index = GPTSimpleVectorIndex(document)
        return index
    def index_load_file(self, file_path):
        index = GPTSimpleVectorIndex.load_from_disk(file_path)
        return index
    def index_discord(self, document):
        index = GPTSimpleVectorIndex(document)
        return index

    def index_webpage(self, url):
        documents = BeautifulSoupWebReader(website_extractor=DEFAULT_WEBSITE_EXTRACTOR).load_data(urls=[url])
        index = GPTSimpleVectorIndex(documents)
        return index

    def reset_indexes(self, user_id):
        self.index_storage[user_id].reset_indexes(user_id)

    async def set_file_index(self, ctx: discord.ApplicationContext, file: discord.Attachment, user_api_key):
        if not user_api_key:
            os.environ["OPENAI_API_KEY"] = self.openai_key
        else:
            os.environ["OPENAI_API_KEY"] = user_api_key
    
        try:
            if file.content_type.startswith("text/plain"):
                suffix = ".txt"
            elif file.content_type.startswith("application/pdf"):
                suffix = ".pdf"
            else:
                await ctx.respond("Only accepts txt or pdf files")
                return
            async with aiofiles.tempfile.TemporaryDirectory() as temp_path:
                async with aiofiles.tempfile.NamedTemporaryFile(suffix=suffix, dir=temp_path, delete=False) as temp_file:
                    await file.save(temp_file.name)
                    index = await self.loop.run_in_executor(None, partial(self.index_file, temp_path))

            file_name = file.filename
            self.index_storage[ctx.user.id].add_index(index, ctx.user.id, file_name)

            await ctx.respond("Index added to your indexes")
        except Exception:
            await ctx.respond("Failed to set index")
            traceback.print_exc()

    async def set_link_index(self, ctx: discord.ApplicationContext, link: str, user_api_key):
        if not user_api_key:
            os.environ["OPENAI_API_KEY"] = self.openai_key
        else:
            os.environ["OPENAI_API_KEY"] = user_api_key

        # TODO Link validation
        try:

            index = await self.loop.run_in_executor(None, partial(self.index_webpage, link))

            # Make the url look nice, remove https, useless stuff, random characters
            file_name = link.replace("https://", "").replace("http://", "").replace("www.", "").replace("/", "_").replace("?", "_").replace("&", "_").replace("=", "_").replace("-", "_").replace(".", "_")

            self.index_storage[ctx.user.id].add_index(index, ctx.user.id, file_name)

        except Exception:
            await ctx.respond("Failed to set index")
            traceback.print_exc()

        await ctx.respond("Index set")


    async def set_discord_index(self, ctx: discord.ApplicationContext, channel: discord.TextChannel, user_api_key):
        if not user_api_key:
            os.environ["OPENAI_API_KEY"] = self.openai_key
        else:
            os.environ["OPENAI_API_KEY"] = user_api_key
    
        try:
            document = await self.load_data(channel_ids=[channel.id], limit=1000, oldest_first=False)
            index = await self.loop.run_in_executor(None, partial(self.index_discord, document))
            self.index_storage[ctx.user.id].add_index(index, ctx.user.id, channel.name)
            await ctx.respond("Index set")
        except Exception:
            await ctx.respond("Failed to set index")
            traceback.print_exc()


    async def load_index(self, ctx:discord.ApplicationContext, index, user_api_key):
        if not user_api_key:
            os.environ["OPENAI_API_KEY"] = self.openai_key
        else:
            os.environ["OPENAI_API_KEY"] = user_api_key

        try:
            index_file = EnvService.find_shared_file(f"indexes/{ctx.user.id}/{index}")
            index = await self.loop.run_in_executor(None, partial(self.index_load_file, index_file))
            self.index_storage[ctx.user.id].queryable_index = index
            await ctx.respond("Loaded index")
        except Exception as e:
            await ctx.respond(e)
    
    
    async def backup_discord(self, ctx: discord.ApplicationContext, user_api_key):
        if not user_api_key:
            os.environ["OPENAI_API_KEY"] = self.openai_key
        else:
            os.environ["OPENAI_API_KEY"] = user_api_key
    
        try:
            channel_ids:List[int] = []
            for c in ctx.guild.text_channels:
                channel_ids.append(c.id)
            document = await self.load_data(channel_ids=channel_ids, limit=3000, oldest_first=False)
            index = await self.loop.run_in_executor(None, partial(self.index_discord, document))
            Path(app_root_path() / "indexes").mkdir(parents = True, exist_ok=True)
            index.save_to_disk(app_root_path() / "indexes" / f"{ctx.guild.name.replace(' ', '-')}_{date.today()}-H{datetime.now().hour}.json")

            await ctx.respond("Backup saved")
        except Exception:
            await ctx.respond("Failed to save backup")
            traceback.print_exc()

    

    async def query(self, ctx: discord.ApplicationContext, query:str, response_mode, user_api_key):
        if not user_api_key:
            os.environ["OPENAI_API_KEY"] = self.openai_key
        else:
            os.environ["OPENAI_API_KEY"] = user_api_key
        
        try:
            index: [GPTSimpleVectorIndex, ComposableGraph] = self.index_storage[ctx.user.id].get_index_or_throw()
            if isinstance(index, GPTSimpleVectorIndex):
                response = await self.loop.run_in_executor(None, partial(index.query, query, verbose=True, text_qa_template=self.qaprompt))
            else:
                response = await self.loop.run_in_executor(None,
                                                           partial(index.query, query, query_configs=[], verbose=True))
            await ctx.respond(f"**Query:**\n\n{query.strip()}\n\n**Query response:**\n\n{response.response.strip()}")
        except Exception:
            traceback.print_exc()
            await ctx.respond("Failed to send query. You may not have an index set, load an index with /index load", delete_after=10)

    # Extracted functions from DiscordReader

    async def read_channel(self, channel_id: int, limit: Optional[int], oldest_first: bool) -> str:
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

            async for msg in channel.history(
                limit=limit, oldest_first=oldest_first
            ):
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
        msg_txt_list = [f"user:{m.author.display_name}, content:{m.content}" for m in messages]

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
            (channel_content, channel_name) = await self.read_channel(channel_id, limit=limit, oldest_first=oldest_first)
            results.append(
                Document(channel_content, extra_info={"channel_name": channel_name})
            )
        return results
