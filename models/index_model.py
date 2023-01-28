import os
import traceback
import asyncio
import tempfile
from functools import partial
import discord

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
    
    
    async def set_index(self, ctx: discord.ApplicationContext, file: discord.Attachment, user_api_key):
        if not user_api_key:
            os.environ["OPENAI_API_KEY"] = self.openai_key
        else:
            os.environ["OPENAI_API_KEY"] = user_api_key
    
        try:
            temp_path = tempfile.TemporaryDirectory()
            temp_file = tempfile.NamedTemporaryFile(suffix=".txt", dir=temp_path.name, delete=False)
            await file.save(temp_file.name)
            index = await self.loop.run_in_executor(None, partial(self.index_file, temp_path.name))
            self.index_storage[ctx.user.id] = index
            temp_path.cleanup()
            await ctx.respond("Index set")
        except Exception:
            await ctx.respond("Failed to set index")
            traceback.print_exc()

    async def query(self, ctx: discord.ApplicationContext, query, user_api_key):
        if not user_api_key:
            os.environ["OPENAI_API_KEY"] = self.openai_key
        else:
            os.environ["OPENAI_API_KEY"] = user_api_key

        if not self.index_storage[ctx.user.id]:
            await ctx.respond("You need to set an index", ephemeral=True, delete_after=5)
            return
        
        index: GPTSimpleVectorIndex = self.index_storage[ctx.user.id]
        response = await self.loop.run_in_executor(None, partial(index.query, query, verbose=True))
        await ctx.respond(f"Query response: {response}")