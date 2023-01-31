import os
import traceback
import asyncio
import tempfile
import discord
from functools import partial
from typing import List, Optional


from gpt_index.readers.base import BaseReader
from gpt_index.readers.schema.base import Document
from gpt_index.response.schema import Response

from gpt_index import GPTSimpleVectorIndex, SimpleDirectoryReader



class Index_handler:
    def __init__(self):
        self.openai_key = os.getenv("OPENAI_TOKEN")
        self.index_storage = {}
        self.loop = asyncio.get_running_loop()
    
    def index_file(self, file_path):
        document = SimpleDirectoryReader(file_path).load_data()
        index = GPTSimpleVectorIndex(document)
        return index
    def index_discord(self, document):
        index = GPTSimpleVectorIndex(document)
        return index
    
    
    async def set_file_index(self, ctx: discord.ApplicationContext, file: discord.Attachment, user_api_key):
        if not user_api_key:
            os.environ["OPENAI_API_KEY"] = self.openai_key
        else:
            os.environ["OPENAI_API_KEY"] = user_api_key
    
        try:
            temp_path = tempfile.TemporaryDirectory()
            if file.content_type.startswith("text/plain"):
                suffix = ".txt"
            elif file.content_type.startswith("application/pdf"):
                suffix = ".pdf"
            else:
                await ctx.respond("Only accepts txt or pdf files")
                return
            temp_file = tempfile.NamedTemporaryFile(suffix=suffix, dir=temp_path.name, delete=False)
            await file.save(temp_file.name)
            index = await self.loop.run_in_executor(None, partial(self.index_file, temp_path.name))
            self.index_storage[ctx.user.id] = index
            temp_path.cleanup()
            await ctx.respond("Index set")
        except Exception:
            await ctx.respond("Failed to set index")
            traceback.print_exc()


    async def set_discord_index(self, ctx: discord.ApplicationContext, channel: discord.TextChannel, user_api_key, no_channel=False):
        if not user_api_key:
            os.environ["OPENAI_API_KEY"] = self.openai_key
        else:
            os.environ["OPENAI_API_KEY"] = user_api_key
    
        try:
            reader = DiscordReader()
            if no_channel:
                channel_ids:List[int] = []
                for c in ctx.guild.text_channels:
                    channel_ids.append(c.id)
                document = await reader.load_data(channel_ids=channel_ids, limit=300, oldest_first=False)
            else:
                document = await reader.load_data(channel_ids=[channel.id], limit=1000, oldest_first=False)
            index = await self.loop.run_in_executor(None, partial(self.index_discord, document))
            self.index_storage[ctx.user.id] = index
            await ctx.respond("Index set")
        except Exception:
            await ctx.respond("Failed to set index")
            traceback.print_exc()

    

    async def query(self, ctx: discord.ApplicationContext, query:str, user_api_key):
        if not user_api_key:
            os.environ["OPENAI_API_KEY"] = self.openai_key
        else:
            os.environ["OPENAI_API_KEY"] = user_api_key

        if not self.index_storage[ctx.user.id]:
            await ctx.respond("You need to set an index", ephemeral=True, delete_after=5)
            return
        
        index: GPTSimpleVectorIndex = self.index_storage[ctx.user.id]
        try:
            response: Response = await self.loop.run_in_executor(None, partial(index.query, query, verbose=True))
        except Exception:
            ctx.respond("You haven't set and index", delete_after=5)
        await ctx.respond(f"**Query:**\n\n{query.strip()}\n\n**Query response:**\n\n{response.response.strip()}")


#Set our own version of the DiscordReader class that's async

class DiscordReader(BaseReader):
    """Discord reader.

    Reads conversations from channels.

    Args:
        discord_token (Optional[str]): Discord token. If not provided, we
            assume the environment variable `DISCORD_TOKEN` is set.

    """

    def __init__(self, discord_token: Optional[str] = None) -> None:
        """Initialize with parameters."""
        if discord_token is None:
            discord_token = os.environ["DISCORD_TOKEN"]
            if discord_token is None:
                raise ValueError(
                    "Must specify `discord_token` or set environment "
                    "variable `DISCORD_TOKEN`."
                )

        self.discord_token = discord_token

    async def read_channel(self, channel_id: int, limit: Optional[int], oldest_first: bool) -> str:
        """Async read channel."""

        messages: List[discord.Message] = []

        class CustomClient(discord.Client):
            async def on_ready(self) -> None:
                try:
                    channel = client.get_channel(channel_id)
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
                        messages.append(msg)
                        if msg.id in thread_dict:
                            thread = thread_dict[msg.id]
                            async for thread_msg in thread.history(
                                limit=limit, oldest_first=oldest_first
                            ):
                                messages.append(thread_msg)
                except Exception as e:
                    print("Encountered error: " + str(e))
                finally:
                    await self.close()

        intents = discord.Intents.default()
        intents.message_content = True
        client = CustomClient(intents=intents)
        await client.start(self.discord_token)

        msg_txt_list = [f"{m.author.display_name}: {m.content}" for m in messages]
        channel = client.get_channel(channel_id)

        return ("\n\n".join(msg_txt_list), channel.name)

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
                Document(channel_content, extra_info={"channel_id": channel_id, "channel_name": channel_name})
            )
        return results