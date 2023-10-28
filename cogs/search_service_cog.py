import datetime
import io
import json
import os
import sys
import tempfile
import traceback
from typing import Optional, Dict, Any

import aiohttp
import re
import discord
import openai
from bs4 import BeautifulSoup
from discord.ext import pages
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

from langchain.agents.conversational_chat.output_parser import ConvoOutputParser

original_parse = ConvoOutputParser.parse


def my_parse(self, text):
    # Remove all pairs of triple backticks from the input. However, don't remove pairs of ```json and ```. Only remove ``` and ``` pairs, maintain the text between the pairs so that only the backticks
    # are removed and the text is left intact.
    text_without_triple_backticks = re.sub(
        r"```(?!json)(.*?)```", r"\1", text, flags=re.DOTALL
    )

    # Call the original parse() method with the modified input
    try:
        result = original_parse(self, text_without_triple_backticks)
    except Exception:
        traceback.print_exc()
        # Take the text and format it like
        # {
        #     "action": "Final Answer",
        #     "action_input": text
        # }
        # This will cause the bot to respond with the text as if it were a final answer.
        if "action_input" not in text_without_triple_backticks:
            text_without_triple_backticks = f'{{"action": "Final Answer", "action_input": {json.dumps(text_without_triple_backticks)}}}'
            result = original_parse(self, text_without_triple_backticks)

        else:
            # Insert "```json" before the opening curly brace
            text_without_triple_backticks = re.sub(
                r"({)", r"```json \1", text_without_triple_backticks
            )

            # Insert "```" after the closing curly brace
            text_without_triple_backticks = re.sub(
                r"(})", r"\1 ```", text_without_triple_backticks
            )

            result = original_parse(self, text_without_triple_backticks)

    return result


# Replace the original parse function with the new one
ConvoOutputParser.parse = my_parse


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

vector_stores = {}


class RedoSearchUser:
    def __init__(self, ctx, query, search_scope, nodes, response_mode):
        self.ctx = ctx
        self.query = query
        self.search_scope = search_scope
        self.nodes = nodes
        self.response_mode = response_mode


class CustomTextRequestWrapper(BaseModel):
    """Lightweight wrapper around requests library.

    The main purpose of this wrapper is to always return a text output.
    """

    headers: Optional[Dict[str, str]] = None
    aiosession: Optional[aiohttp.ClientSession] = None

    class Config:
        """Configuration for this pydantic object."""

        extra = Extra.forbid
        arbitrary_types_allowed = True

    def __init__(self, **data: Any):
        super().__init__(**data)

    @property
    def requests(self) -> Requests:
        return Requests(headers=self.headers, aiosession=self.aiosession)

    def get(self, url: str, **kwargs: Any) -> str:
        # the "url" field is actuall some input from the LLM, it is a comma separated string of the url and a boolean value and the original query
        try:
            url, model, original_query = url.split(",")
            url = url.strip()
            model = model.strip()
            original_query = original_query.strip()
        except:
            url = url
            model = "gpt-3.5-turbo"
            original_query = "No Original Query Provided"

        """GET the URL and return the text."""
        text = self.requests.get(url, **kwargs).text

        # Load this text into BeautifulSoup, clean it up and only retain text content within <p> and <title> and <h1> type tags, get rid of all javascript and css too.
        soup = BeautifulSoup(text, "html.parser")

        # Decompose script, style, head, and meta tags
        for tag in soup(["script", "style", "head", "meta"]):
            tag.decompose()

        # Get remaining text from the soup object
        text = soup.get_text()

        # Clean up white spaces
        text = re.sub(r"\s+", " ", text).strip()

        # If not using GPT-4 and the text token amount is over 3500, truncate it to 3500 tokens
        enc = tiktoken.encoding_for_model(model)
        tokens = len(enc.encode(text))
        if len(text) < 5:
            return "This website could not be scraped. I cannot answer this question."
        if (
            model in Models.CHATGPT_MODELS
            and tokens > Models.get_max_tokens(model) - 1000
        ) or (
            model in Models.GPT4_MODELS and tokens > Models.get_max_tokens(model) - 1000
        ):
            with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
                f.write(text)
                f.close()
                document = SimpleDirectoryReader(input_files=[f.name]).load_data()
                embed_model = OpenAIEmbedding()
                service_context = ServiceContext.from_defaults(embed_model=embed_model)
                index = GPTVectorStoreIndex.from_documents(
                    document, service_context=service_context, use_async=True
                )
                retriever = VectorIndexRetriever(
                    index=index, similarity_top_k=4, service_context=service_context
                )
                response_synthesizer = get_response_synthesizer(
                    response_mode=ResponseMode.COMPACT,
                    refine_template=CHAT_REFINE_PROMPT,
                    service_context=service_context,
                    use_async=True,
                )
                query_engine = RetrieverQueryEngine(
                    retriever=retriever, response_synthesizer=response_synthesizer
                )
                response_text = query_engine.query(original_query)
                return response_text

        return text


class SearchService(discord.Cog, name="SearchService"):
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
        self.model = Search(gpt_model, usage_service)
        self.EMBED_CUTOFF = 2000
        self.redo_users = {}
        self.chat_agents = {}
        self.thread_awaiting_responses = []
        self.converser_cog = converser_cog
        # Make a mapping of all the country codes and their full country names:

    async def paginate_embed(
        self, response_text, user: discord.Member, original_link=None
    ):
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
                    title="Search Results"
                    if not original_link
                    else "Follow-up results",
                    description=chunk,
                    url=original_link,
                )
                first = True
            else:
                page = discord.Embed(
                    title=f"Page {count}",
                    description=chunk,
                    url=original_link,
                )
            if user.avatar:
                page.set_footer(
                    text=f"Requested by {user.name}", icon_url=user.avatar.url
                )
            else:
                page.set_footer(
                    text=f"Requested by {user.name}", icon_url=user.default_avatar.url
                )
            pages.append(page)

        return pages

    async def paginate_chat_embed(self, response_text):
        """Given a response text make embed pages and return a list of the pages."""

        response_text = [
            response_text[i : i + 3500] for i in range(0, len(response_text), 7000)
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
            used_tools = []
            try:
                # Start listening to STDOUT before this call. We wanna track all the output for this specific call below
                response, stdout_output = await capture_stdout(
                    self.bot.loop.run_in_executor, None, agent.run, prompt
                )
                response = str(response)

                try:
                    print(stdout_output)
                except:
                    traceback.print_exc()
                    stdout_output = ""

                if "Wolfram-Tool" in stdout_output:
                    used_tools.append("Wolfram Alpha")
                if "Search-Tool" in stdout_output:
                    used_tools.append("Google Search")
                if "Web-Crawling-Tool" in stdout_output:
                    used_tools.append("Web Crawler")

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
                await paginator.respond(message)
            else:
                response = response.replace("\\n", "\n")
                # Build a response embed
                response_embed = discord.Embed(
                    title="",
                    description=response,
                    color=0x808080,
                )
                if len(used_tools) > 0:
                    response_embed.set_footer(
                        text="Used tools: " + ", ".join(used_tools)
                    )
                await message.reply(embed=response_embed)

            self.thread_awaiting_responses.remove(message.channel.id)

    async def search_chat_command(
        self, ctx: discord.ApplicationContext, model, search_scope=2
    ):
        embed_title = f"{ctx.user.name}'s internet-connected conversation with GPT"
        message_embed = discord.Embed(
            title=embed_title,
            description=f"The agent will visit and browse **{search_scope}** link(s) every time it needs to access the internet.\nCrawling is enabled, send the bot a link for it to access it!\nModel: {model}\n\nType `end` to stop the conversation",
            color=0xBA6093,
        )
        message_embed.set_thumbnail(url="https://i.imgur.com/sioynYZ.png")
        message_embed.set_footer(
            text="Internet Chat", icon_url="https://i.imgur.com/sioynYZ.png"
        )
        message_thread = await ctx.send(embed=message_embed)
        thread = await message_thread.create_thread(
            name=ctx.user.name + "'s internet-connected conversation with GPT",
            auto_archive_duration=60,
        )
        await ctx.respond("Conversation started.")
        print("The search scope is " + str(search_scope) + ".")

        # Make a new agent for this user to chat.
        search = GoogleSearchAPIWrapper(
            google_api_key=GOOGLE_API_KEY,
            google_cse_id=GOOGLE_SEARCH_ENGINE_ID,
            k=search_scope,
        )

        requests = CustomTextRequestWrapper()

        tools = [
            Tool(
                name="Search-Tool",
                func=search.run,
                description="useful when you need to answer questions about current events or retrieve information about a topic that may require the internet. The input to this tool is a search query to ask google. Search queries should be less than 8 words. For example, an input could be 'What is the weather like in New York?' and the tool input would be 'weather new york'.",
            ),
            # The requests tool
            Tool(
                name="Web-Crawling-Tool",
                func=requests.get,
                description=f"Useful for when the user provides you with a website link, use this tool to crawl the website and retrieve information from it. The input to this tool is a comma separated list of three values, the first value is the link to crawl for, and the second value is {model} and is the GPT model used, and the third value is the original question that the user asked. For example, an input could be 'https://google.com', gpt-3.5-turbo, 'What is this webpage?'. This tool should only be used if a direct link is provided and not in conjunction with other tools.",
            ),
        ]

        # Try to add wolfram tool
        try:
            wolfram = WolframAlphaAPIWrapper(wolfram_alpha_appid=WOLFRAM_API_KEY)
            tools.append(
                Tool(
                    name="Wolfram-Tool",
                    func=wolfram.run,
                    description="useful when you need to answer questions about math, solve equations, do proofs, mathematical science questions, science questions, and when asked to do numerical based reasoning.",
                )
            )
            print("Wolfram tool added to internet-connected conversation agent.")
        except Exception:
            traceback.print_exc()
            print("Wolfram tool not added to internet-connected conversation agent.")

        memory = ConversationBufferMemory(
            memory_key="chat_history", return_messages=True
        )

        llm = ChatOpenAI(model=model, temperature=0, openai_api_key=OPENAI_API_KEY)

        agent_chain = initialize_agent(
            tools,
            llm,
            agent=AgentType.CHAT_CONVERSATIONAL_REACT_DESCRIPTION,
            verbose=True,
            memory=memory,
            max_execution_time=120,
            max_iterations=4,
            early_stopping_method="generate",
        )

        self.chat_agents[thread.id] = agent_chain

    async def search_command(
        self,
        ctx: discord.ApplicationContext,
        query,
        search_scope,
        nodes,
        deep,
        response_mode,
        model,
        multistep=False,
        redo=None,
        from_followup=None,
        followup_user=None,
    ):
        """Command handler for the search command"""
        await ctx.defer() if not redo else None

        # Check the opener for bad content.
        if PRE_MODERATE:
            if await Moderation.simple_moderate_and_respond(query, ctx):
                return

        user_api_key = None
        if USER_INPUT_API_KEYS:
            user_api_key = await TextService.get_user_api_key(
                ctx.user.id, ctx, USER_KEY_DB
            )
            if not user_api_key:
                return

        if (
            not EnvService.get_google_search_api_key()
            or not EnvService.get_google_search_engine_id()
        ):
            await ctx.respond(
                embed=EmbedStatics.get_search_failure_embed(
                    str("The search service is not enabled on this server.")
                ),
            )
            return

        try:
            response, refined_text = await self.model.search(
                ctx,
                query,
                user_api_key,
                search_scope,
                nodes,
                deep,
                response_mode,
                model,
                multistep,
            )
        except ValueError as e:
            traceback.print_exc()
            await ctx.respond(
                embed=EmbedStatics.get_search_failure_embed(str(e)),
                ephemeral=True,
            )
            return
        except Exception as e:
            await ctx.respond(
                embed=EmbedStatics.get_search_failure_embed(str(e)), ephemeral=True
            )
            traceback.print_exc()
            return

        url_extract_pattern = "https?:\\/\\/(?:www\\.)?[-a-zA-Z0-9@:%._\\+~#=]{1,256}\\.[a-zA-Z0-9()]{1,6}\\b(?:[-a-zA-Z0-9()@:%_\\+.~#?&\\/=]*)"
        urls = re.findall(
            url_extract_pattern,
            str(response.get_formatted_sources(length=200)),
            flags=re.IGNORECASE,
        )
        urls = "\n".join(f"<{url}>" for url in urls)

        # Deduplicate the urls
        urls = "\n".join(dict.fromkeys(urls.split("\n")))

        if from_followup:
            original_link, followup_question = (
                from_followup.original_link,
                from_followup.followup_question,
            )
            query_response_message = f"**Question:**\n\n`{followup_question}`\n\n**Google Search Query**\n\n`{refined_text.strip()}`\n\n**Final Answer:**\n\n{response.response.strip()}\n\n**Sources:**\n{urls}"
        else:
            query_response_message = f"**Question:**\n\n`{query.strip()}`\n\n**Google Search Query**\n\n`{refined_text.strip()}`\n\n**Final Answer:**\n\n{response.response.strip()}\n\n**Sources:**\n{urls}"
        query_response_message = query_response_message.replace(
            "<|endofstatement|>", ""
        )
        query_response_message = query_response_message.replace(
            "Answer to original:\n", ""
        )
        query_response_message = query_response_message.replace(
            "Answer to follow-up:\n", ""
        )

        # If the response is too long, lets paginate using the discord pagination
        # helper
        embed_pages = await self.paginate_embed(
            query_response_message,
            ctx.user if not followup_user else followup_user,
            original_link if from_followup else None,
        )
        paginator = pages.Paginator(
            pages=embed_pages,
            timeout=None,
            author_check=False,
            custom_view=SearchView(ctx, self, query_response_message),
        )

        self.redo_users[ctx.user.id] = RedoSearchUser(
            ctx, query, search_scope, nodes, response_mode
        )

        await paginator.respond(ctx.interaction)


class SearchView(discord.ui.View):
    def __init__(
        self,
        ctx,
        search_cog,
        response_text,
    ):
        super().__init__(timeout=None)  # No timeout
        self.search_cog = search_cog
        self.ctx = ctx
        self.response_text = response_text
        self.add_item(RedoButton(self.ctx, self.search_cog))
        self.add_item(FollowupButton(self.ctx, self.search_cog, self.response_text))


# A view for a follow-up button
class FollowupButton(discord.ui.Button["SearchView"]):
    def __init__(self, ctx, search_cog, response_text):
        super().__init__(label="Follow Up", style=discord.ButtonStyle.green)
        self.search_cog = search_cog
        self.ctx = ctx
        self.response_text = response_text

    async def callback(self, interaction: discord.Interaction):
        """Send the followup modal"""
        await interaction.response.send_modal(
            modal=FollowupModal(self.ctx, self.search_cog, self.response_text)
        )


# A view for a redo button
class RedoButton(discord.ui.Button["SearchView"]):
    def __init__(self, ctx, search_cog):
        super().__init__(
            style=discord.ButtonStyle.danger,
            label="Redo",
            custom_id="redo_search_button",
        )
        self.ctx = ctx
        self.search_cog = search_cog

    async def callback(self, interaction: discord.Interaction):
        """Redo the search"""
        await interaction.response.send_message(
            embed=EmbedStatics.get_search_redo_progress_embed(),
            ephemeral=True,
            delete_after=15,
        )
        await self.search_cog.search_command(
            self.search_cog.redo_users[self.ctx.user.id].ctx,
            self.search_cog.redo_users[self.ctx.user.id].query,
            self.search_cog.redo_users[self.ctx.user.id].search_scope,
            self.search_cog.redo_users[self.ctx.user.id].nodes,
            deep=False,
            redo=True,
            response_mode=self.search_cog.redo_users[self.ctx.user.id].response_mode,
        )


class FollowupData:
    def __init__(self, original_link, followup_question):
        self.original_link = original_link
        self.followup_question = followup_question


# The modal for following up
class FollowupModal(discord.ui.Modal):
    def __init__(self, ctx, search_cog, response_text) -> None:
        super().__init__(title="Search Follow-up")
        # Get the argument named "user_key_db" and save it as USER_KEY_DB
        self.search_cog = search_cog
        self.ctx = ctx
        self.response_text = response_text

        self.add_item(
            discord.ui.InputText(
                label="What other questions do you have?",
                placeholder="",
            )
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        query = self.search_cog.redo_users[self.ctx.user.id].query

        # In the response text, get only the text between "**Final Answer:**" and "**Sources:**"
        self.response_text = self.response_text.split("**Final Answer:**")[1].split(
            "**Sources:**"
        )[0]

        # Build the context
        context_text = (
            "Original question: "
            + query
            + "\n"
            + "Answer to original: "
            + self.response_text
            + "\n"
            + "Follow-up question: "
            + self.children[0].value
        )

        # Get the link of the message that the user interacted on
        message_link = f"https://discord.com/channels/{interaction.guild_id}/{interaction.channel_id}/{interaction.message.id}"

        await self.search_cog.search_command(
            self.search_cog.redo_users[self.ctx.user.id].ctx,
            context_text,
            self.search_cog.redo_users[self.ctx.user.id].search_scope,
            self.search_cog.redo_users[self.ctx.user.id].nodes,
            deep=False,
            redo=True,
            from_followup=FollowupData(message_link, self.children[0].value),
            response_mode=self.search_cog.redo_users[self.ctx.user.id].response_mode,
            followup_user=interaction.user,
            model="gpt-4-32k",
        )
