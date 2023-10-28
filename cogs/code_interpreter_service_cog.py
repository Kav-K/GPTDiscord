import asyncio
import datetime
import functools
import io
import json
import os
import sys
import tempfile
import traceback
from concurrent.futures.thread import ThreadPoolExecutor
from typing import Optional, Dict, Any

import aiohttp
import re
import discord
import openai
from bs4 import BeautifulSoup
from discord.ext import pages
from e2b import Session, DataAnalysis
from langchain import (
    GoogleSearchAPIWrapper,
    WolframAlphaAPIWrapper,
    FAISS,
    InMemoryDocstore,
    LLMChain,
    ConversationChain,
)
from langchain.agents import (
    Tool,
    initialize_agent,
    AgentType,
    ZeroShotAgent,
    AgentExecutor,
)
from langchain.chat_models import ChatOpenAI
from langchain.memory import ConversationBufferMemory, CombinedMemory
from langchain.prompts import (
    ChatPromptTemplate,
    SystemMessagePromptTemplate,
    MessagesPlaceholder,
    HumanMessagePromptTemplate,
)
from langchain.requests import TextRequestsWrapper, Requests
from langchain.schema import SystemMessage
from llama_index import (
    GPTVectorStoreIndex,
    Document,
    SimpleDirectoryReader,
    ServiceContext,
    OpenAIEmbedding,
)
from llama_index.response_synthesizers import get_response_synthesizer, ResponseMode
from llama_index.retrievers import VectorIndexRetriever
from llama_index.query_engine import RetrieverQueryEngine
from llama_index.prompts.chat_prompts import CHAT_REFINE_PROMPT
from pydantic import Extra, BaseModel
import tiktoken

from models.embed_statics_model import EmbedStatics
from models.search_model import Search
from services.deletion_service import Deletion
from services.environment_service import EnvService
from services.moderations_service import Moderation
from services.text_service import TextService
from models.openai_model import Models

from contextlib import redirect_stdout


class CaptureStdout:
    def __enter__(self):
        self.buffer = io.StringIO()
        self.original_stdout = sys.stdout
        sys.stdout = self.buffer
        return self.buffer

    def __exit__(self, exc_type, exc_val, exc_tb):
        sys.stdout = self.original_stdout


async def capture_stdout(func, *args, **kwargs):
    with CaptureStdout() as buffer:
        result = await func(*args, **kwargs)
    captured_output = buffer.getvalue()
    return result, captured_output


ALLOWED_GUILDS = EnvService.get_allowed_guilds()
USER_INPUT_API_KEYS = EnvService.get_user_input_api_keys()
USER_KEY_DB = EnvService.get_api_db()
PRE_MODERATE = EnvService.get_premoderate()
GOOGLE_API_KEY = EnvService.get_google_search_api_key()
GOOGLE_SEARCH_ENGINE_ID = EnvService.get_google_search_engine_id()
OPENAI_API_KEY = EnvService.get_openai_token()
# Set the environment
os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY
openai.api_key = os.environ["OPENAI_API_KEY"]

WOLFRAM_API_KEY = EnvService.get_wolfram_api_key()
E2B_API_KEY = EnvService.get_e2b_api_key()

vector_stores = {}


class CodeInterpreterService(discord.Cog, name="CodeInterpreterService"):
    """Cog containing translation commands and retrieval of translation services"""

    def __init__(
            self,
            bot,
            gpt_model,
            usage_service,
            deletion_service,
            converser_cog,
    ):
        super().__init__()
        self.bot = bot
        self.usage_service = usage_service
        self.EMBED_CUTOFF = 2000
        self.redo_users = {}
        self.chat_agents = {}
        self.thread_awaiting_responses = []
        self.converser_cog = converser_cog
        self.session = DataAnalysis(api_key=E2B_API_KEY)
        self.executor = ThreadPoolExecutor(max_workers=10)
        # Make a mapping of all the country codes and their full country names:

    async def paginate_chat_embed(self, response_text):
        """Given a response text make embed pages and return a list of the pages."""

        response_text = [
            response_text[i: i + 3500] for i in range(0, len(response_text), 7000)
        ]
        pages = []
        first = False
        # Send each chunk as a message
        for count, chunk in enumerate(response_text, start=1):
            if not first:
                page = discord.Embed(
                    title=f"{count}",
                    description=chunk,
                )
                first = True
            else:
                page = discord.Embed(
                    title=f"{count}",
                    description=chunk,
                )
            pages.append(page)

        return pages

    @discord.Cog.listener()
    async def on_message(self, message):
        # Check if the message is from a bot.
        if message.author.id == self.bot.user.id:
            return

        # Check if the message is from a guild.
        if not message.guild:
            return

        # System message
        if message.type != discord.MessageType.default:
            return

        if message.content.strip().startswith("~"):
            return

        # if we are still awaiting a response from the agent, then we don't want to process the message.
        if message.channel.id in self.thread_awaiting_responses:
            resp_message = await message.reply(
                "Please wait for the agent to respond to a previous message first!"
            )
            deletion_time = datetime.datetime.now() + datetime.timedelta(seconds=5)
            deletion_time = deletion_time.timestamp()

            original_deletion_message = Deletion(message, deletion_time)
            deletion_message = Deletion(resp_message, deletion_time)
            await self.converser_cog.deletion_queue.put(deletion_message)
            await self.converser_cog.deletion_queue.put(original_deletion_message)
            return

        # Pre moderation
        if PRE_MODERATE:
            if await Moderation.simple_moderate_and_respond(message.content, message):
                await message.delete()
                return

        prompt = message.content.strip()

        # If the message channel is in self.chat_agents, then we delegate the message to the agent.
        if message.channel.id in self.chat_agents:
            if prompt.lower() in ["stop", "end", "quit", "exit"]:
                await message.reply("Ending chat session.")
                self.chat_agents.pop(message.channel.id)

                # close the thread
                thread = await self.bot.fetch_channel(message.channel.id)
                await thread.edit(name="Closed-GPT")
                await thread.edit(archived=True)
                return

            self.thread_awaiting_responses.append(message.channel.id)

            try:
                await message.channel.trigger_typing()
            except:
                pass

            agent = self.chat_agents[message.channel.id]
            try:
                # Start listening to STDOUT before this call. We wanna track all the output for this specific call below
                response, stdout_output = await capture_stdout(
                    self.bot.loop.run_in_executor, None, agent.run, prompt
                )
                response = str(response)

                try:
                    print(response)
                    print(stdout_output)
                except:
                    pass

            except Exception as e:
                response = f"Error: {e}"
                traceback.print_exc()
                await message.reply(
                    embed=EmbedStatics.get_internet_chat_failure_embed(response)
                )
                self.thread_awaiting_responses.remove(message.channel.id)
                return

            if len(response) > 2000:
                embed_pages = await self.paginate_chat_embed(response)
                paginator = pages.Paginator(
                    pages=embed_pages,
                    timeout=None,
                    author_check=False,
                )
                try:
                    await paginator.respond(message)
                except:
                    response = [
                        response[i: i + 1900] for i in range(0, len(response), 1900)
                    ]
                    for count, chunk in enumerate(response, start=1):
                            await message.channel.send(chunk)

            else:
                response = response.replace("\\n", "\n")
                # Build a response embed
                response_embed = discord.Embed(
                    title="",
                    description=response,
                    color=0x808080,
                )
                await message.reply(embed=response_embed)

            self.thread_awaiting_responses.remove(message.channel.id)

    def execute_code_sync(self, code: str):
        """Synchronous wrapper around the async execute_code function."""
        return asyncio.run(self.execute_code_async(code))

    async def execute_code_async(self, code: str):
        loop = asyncio.get_running_loop()
        runner = functools.partial(self.session.run_python, code=code, timeout=5)

        stdout, stderr, artifacts = await loop.run_in_executor(None, runner)
        if len(stdout) > 12000:
            stdout = stdout[:12000]
        return stdout

    async def code_interpreter_chat_command(
            self, ctx: discord.ApplicationContext, model,
    ):
        embed_title = f"{ctx.user.name}'s code interpreter conversation with GPT"
        message_embed = discord.Embed(
            title=embed_title,
            description=f"The agent is able to execute Python code and manipulate its environment.\nModel: {model}\n\nType `end` to stop the conversation",
            color=0xf82c45,
        )
        message_embed.set_thumbnail(url="https://i.imgur.com/qua6Bya.png")
        message_embed.set_footer(
            text="Code Interpreter Chat", icon_url="https://i.imgur.com/qua6Bya.png"
        )
        message_thread = await ctx.send(embed=message_embed)
        thread = await message_thread.create_thread(
            name=ctx.user.name + "'s code interpreter conversation with GPT",
            auto_archive_duration=60,
        )
        await ctx.respond("Conversation started.")

        tools = [
            # The requests tool
            Tool(
                name="Code-execution-tool",
                func=self.execute_code_sync,
                description=f"This tool is able to execute Python 3 code. The input to the tool is just the raw python code. The output is the stdout of the code. When using the output of the code execution tool, always make sure to always display the raw output to the user as well.",
            ),
        ]

        memory = ConversationBufferMemory(memory_key="memory", return_messages=True)

        agent_kwargs = {
            "extra_prompt_messages": [MessagesPlaceholder(variable_name="memory")],
            "system_message": SystemMessage(
                content="You are an expert programmer that is able to use the tools to your advantage to execute python code. Help the user iterate on their code and test it through execution. Always respond in the specified JSON format. Always provide the full code output when asked for when you execute code. Ensure that all your code is formatted with backticks followed by the markdown identifier of the language that the code is in. For example ```python3 {code} ```.")
        }

        llm = ChatOpenAI(model=model, temperature=0, openai_api_key=OPENAI_API_KEY)

        agent_chain = initialize_agent(
            tools=tools,
            llm=llm,
            agent=AgentType.OPENAI_FUNCTIONS,
            verbose=True,
            agent_kwargs=agent_kwargs,
            memory=memory,
        )

        self.chat_agents[thread.id] = agent_chain
