import asyncio
import datetime
import functools
import io
import os
import sys
import tempfile
import traceback
from concurrent.futures.thread import ThreadPoolExecutor
from typing import List

import re

import aiofiles
import discord
import openai
from discord.ext import pages
from e2b import Session, DataAnalysis
from e2b.templates.data_analysis import Artifact

from langchain.agents import (
    Tool,
    initialize_agent,
    AgentType,
)
from langchain.chat_models import ChatOpenAI
from langchain.memory import ConversationBufferMemory, ConversationSummaryBufferMemory
from langchain.prompts import (
    MessagesPlaceholder,
)
from langchain.schema import SystemMessage
from langchain.utilities import GoogleSearchAPIWrapper

from models.embed_statics_model import EmbedStatics
from services.deletion_service import Deletion
from services.environment_service import EnvService
from services.moderations_service import Moderation


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

OPENAI_API_KEY = EnvService.get_openai_token()
# Set the environment
os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY
openai.api_key = os.environ["OPENAI_API_KEY"]
GOOGLE_API_KEY = EnvService.get_google_search_api_key()
GOOGLE_SEARCH_ENGINE_ID = EnvService.get_google_search_engine_id()

E2B_API_KEY = EnvService.get_e2b_api_key()


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
        self.executor = ThreadPoolExecutor(max_workers=10)
        self.initial_messages = {}
        self.sessions = {}
        # Make a mapping of all the country codes and their full country names:

    @discord.Cog.listener()
    async def on_message(self, message):
        # Check if the message is from a bot.
        if message.author == self.bot.user:
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
                await message.reply(
                    "Ending chat session. You can access the sandbox of this session at https://"
                    + self.sessions[message.channel.id].get_hostname()
                )
                self.sessions[message.channel.id].close()
                self.chat_agents.pop(message.channel.id)

                # close the thread
                thread = await self.bot.fetch_channel(message.channel.id)
                await thread.edit(name="Closed-GPT")
                await thread.edit(archived=True)
                return

            file = message.attachments[0] if len(message.attachments) > 0 else None

            # File operations, allow for user file upload
            if file:
                # We will attempt to upload the file to the execution environment
                thinking_embed = discord.Embed(
                    title=f"🤖💬 Uploading file to code interpreter environment...",
                    color=0x808080,
                )

                thinking_embed.set_footer(text="This may take a few seconds.")
                try:
                    thinking_message = await message.reply(embed=thinking_embed)
                except:
                    traceback.print_exc()
                    pass

                try:
                    await message.channel.trigger_typing()
                except Exception:
                    pass

                async with aiofiles.tempfile.NamedTemporaryFile(
                    delete=False
                ) as temp_file:
                    await file.save(temp_file.name)

                    filename = file.filename

                    # Assert that the filename is < 100 characters, if it is greater, truncate to the first 100 characters and keep the original ending
                    if len(filename) > 100:
                        filename = filename[:100] + filename[-4:]

                    file_upload_result = await self.sessions[
                        message.channel.id
                    ].upload_file_async(filename, await file.read())

                    if filename in str(file_upload_result):
                        try:
                            await thinking_message.delete()
                            prompt += (
                                "\n{The user has just uploaded a file to "
                                + f"/home/user/{filename}"
                                + "}"
                            )
                            print("The edited prompt is: " + prompt)
                        except:
                            traceback.print_exc()
                            pass
                    else:
                        try:
                            failed_embed = discord.Embed(
                                title=f"🤖💬 File upload failed", color=0x808080
                            )
                            await message.reply(embed=failed_embed)
                            return
                        except:
                            traceback.print_exc()
                            pass

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
                    embed=EmbedStatics.get_code_chat_failure_embed(response)
                )
                self.thread_awaiting_responses.remove(message.channel.id)
                return

            # Parse the artifact names. After Artifacts: there should be a list in form [] where the artifact names are inside, comma separated inside stdout_output
            artifact_names = re.findall(r"Artifacts: \[(.*?)\]", stdout_output)
            # The artifacts list may be formatted like ["'/home/user/artifacts/test2.txt', '/home/user/artifacts/test.txt'"], where its technically 1 element in the list, so we need to split it by comma and then remove the quotes and spaces
            if len(artifact_names) > 0:
                artifact_names = artifact_names[0].split(",")
                artifact_names = [
                    artifact_name.strip().replace("'", "")
                    for artifact_name in artifact_names
                ]

            artifacts_available = len(artifact_names) > 0

            if len(response) > 2000:
                embed_pages = await EmbedStatics.paginate_chat_embed(response)
                paginator = pages.Paginator(
                    pages=embed_pages,
                    timeout=None,
                    author_check=False,
                    custom_view=CodeInterpreterDownloadArtifactsView(
                        message, self, self.sessions[message.channel.id], artifact_names
                    )
                    if artifacts_available
                    else None,
                )
                try:
                    await paginator.respond(message)
                except:
                    response = [
                        response[i : i + 1900] for i in range(0, len(response), 1900)
                    ]
                    for count, chunk in enumerate(response, start=1):
                        await message.channel.send(chunk)
                    if artifacts_available:
                        await message.channel.send(
                            "Retrieve your artifacts",
                            view=CodeInterpreterDownloadArtifactsView(
                                message,
                                self,
                                self.sessions[message.channel.id],
                                artifact_names,
                            ),
                        )

            else:
                response = response.replace("\\n", "\n")
                # Build a response embed
                response_embed = discord.Embed(
                    title="",
                    description=response,
                    color=0x808080,
                )
                await message.reply(
                    embed=response_embed,
                    view=CodeInterpreterDownloadArtifactsView(
                        message, self, self.sessions[message.channel.id], artifact_names
                    )
                    if artifacts_available
                    else None,
                )

            self.thread_awaiting_responses.remove(message.channel.id)

    class SessionedCodeExecutor:
        def __init__(self):
            try:
                self.session = DataAnalysis(api_key=E2B_API_KEY)
                self.sessioned = True
            except:
                traceback.print_exc()
                self.sessioned = False

        def execute_code_sync(self, code: str):
            """Synchronous wrapper around the async execute_code function."""
            return asyncio.run(self.execute_code_async(code))

        async def execute_code_async(self, code: str):
            loop = asyncio.get_running_loop()
            runner = functools.partial(self.session.run_python, code=code, timeout=30)

            stdout, stderr, artifacts = await loop.run_in_executor(None, runner)
            artifacts: List[Artifact] = list(artifacts)

            artifacts_or_no_artifacts = (
                "\nArtifacts: " + str([artifact.name for artifact in artifacts])
                if len(artifacts) > 0
                else "\nNO__ARTIFACTS"
            )

            if len(stdout) > 12000:
                stdout = stdout[:12000]
            return (
                "STDOUT: " + stdout + "\nSTDERR: " + stderr + artifacts_or_no_artifacts
            )

        def close(self):
            self.session.close()

        def get_hostname(self):
            return self.session.get_hostname()

        def download_file(self, filepath):
            return self.session.download_file(filepath, timeout=30)

        def install_python_package(self, package):
            return self.session.install_python_packages(package_names=package)

        def install_system_package(self, package):
            return self.session.install_system_packages(package_names=package)

        def run_command_sync(self, command):
            return asyncio.run(self.run_command_async(command))

        async def run_command_async(self, command):
            loop = asyncio.get_running_loop()
            runner = functools.partial(
                self.session.process.start, cmd=command, timeout=30
            )

            command = await loop.run_in_executor(None, runner)

            runner = functools.partial(command.wait)
            await loop.run_in_executor(None, runner)

            output = "STDOUT:" + command.stdout + "\nSTDERR:" + command.stderr
            return output

        def is_sessioned(self):
            return self.sessioned

        def upload_file_sync(self, path, file):
            return asyncio.run(self.upload_file_async(path, file))

        async def upload_file_async(self, path, file):
            loop = asyncio.get_running_loop()
            runner = functools.partial(
                self.session.filesystem.write_bytes,
                path=f"/home/user/{path}",
                content=file,
            )

            await loop.run_in_executor(None, runner)

            runner = functools.partial(
                self.session.filesystem.list, path=f"/home/user/"
            )
            list_output = await loop.run_in_executor(None, runner)

            return list_output

    async def code_interpreter_chat_command(
        self,
        ctx: discord.ApplicationContext,
        model,
    ):
        embed_title = f"{ctx.user.name}'s code interpreter conversation with GPT"
        message_embed = discord.Embed(
            title=embed_title,
            description=f"The agent is able to execute Python code and manipulate its environment.\nModel: {model}\n\nType `end` to stop the conversation",
            color=0xF82C45,
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

        self.sessions[thread.id] = self.SessionedCodeExecutor()

        if not self.sessions[thread.id].is_sessioned():
            await thread.send(
                "Failed to start code interpreter session. This may be an issue with E2B. Please try again later."
            )
            await thread.edit(name="Closed-GPT (Error)")
            await thread.edit(archived=True)
            return

        tools = [
            # The requests tool
            Tool(
                name="Code-execution-tool",
                func=self.sessions[thread.id].execute_code_sync,
                description=f"This tool is able to execute Python 3 code. The input to the tool is just the raw python code. The output is the stdout of the code. When using the output of the code execution tool, always make sure to always display the raw output to the user as well.",
            ),
            Tool(
                name="Install-python-package-tool",
                func=self.sessions[thread.id].install_python_package,
                description=f"This tool installs a python package into the execution environment. The input to the tool is a single python package name (e.g 'numpy'). If you need to install multiple python packages, call this tool multiple times.",
            ),
            Tool(
                name="Install-system-package-tool",
                func=self.sessions[thread.id].install_python_package,
                description=f"This tool installs a system package into the system environment. The input to the tool is a single package name (e.g 'htop'). If you need to install multiple system packages, call this tool multiple times.",
            ),
            Tool(
                name="Run-command-tool",
                func=self.sessions[thread.id].run_command_sync,
                description=f"This tool allows you to run terminal (bash/unix) commands in the execution environment. The input to the tool is the command to run. An example input can be 'df -h'",
            ),
        ]

        # Add google search functionality if the user has google keys set up
        if GOOGLE_API_KEY and GOOGLE_SEARCH_ENGINE_ID:
            search = GoogleSearchAPIWrapper(
                google_api_key=GOOGLE_API_KEY,
                google_cse_id=GOOGLE_SEARCH_ENGINE_ID,
                k=2,
            )
            tools.append(
                Tool(
                    name="Search-Tool",
                    func=search.run,
                    description="This tool is useful when you need to answer questions about current events or retrieve information about a topic that may require the internet. The input to this tool is a search query to ask google. Search queries should be less than 8 words. For example, an input could be 'What is the weather like in New York?' and the tool input would be 'weather new york'.",
                )
            )

        llm = ChatOpenAI(model=model, temperature=0, openai_api_key=OPENAI_API_KEY)

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
                content="You are an expert programmer that is able to use the tools to your advantage to execute "
                "python code. Help the user iterate on their code and test it through execution. Always "
                "respond in the specified JSON format. Always provide the full code output when asked for "
                "when you execute code. Ensure that all your code is formatted with backticks followed by the "
                "markdown identifier of the language that the code is in. For example ```python3 {code} ```. You are "
                "able to search the internet to find the most up to date algorithms and practices. You are "
                "also able to run commands in the execution environment such as to work with files, "
                "make curl requests, or etc. The environment is Linux. When asked to write code that saves "
                "files, always prefix the file with the artifacts/ folder. For example, if asked to create "
                "test.txt, in the function call you make to whatever library that creates the file, "
                "you would use artifacts/test.txt. However, when users upload files, they will by default be "
                "in /home/user/, so if working with a user file, unless they give you the full path, "
                "look in /home/user. Always show the output of code execution explicitly and separately at "
                "the end of the rest of your output. You are also able to install system and python packages "
                "using your tools. However, the tools can only install one package at a time, if you need to "
                "install multiple packages, call the tools multiple times. Always first display your code to "
                "the user BEFORE you execute it using your tools. The user should always explicitly ask you "
                "to execute code. Never execute code before showing the user the code first."
            ),
        }

        agent_chain = initialize_agent(
            tools=tools,
            llm=llm,
            agent=AgentType.OPENAI_FUNCTIONS,
            verbose=True,
            agent_kwargs=agent_kwargs,
            memory=memory,
            handle_parsing_errors="Check your output and make sure it conforms!",
            max_iterations=5,
        )

        self.chat_agents[thread.id] = agent_chain


class CodeInterpreterDownloadArtifactsView(discord.ui.View):
    def __init__(
        self,
        ctx,
        code_interpreter_cog,
        session,
        artifact_names,
    ):
        super().__init__(timeout=None)  # No timeout
        self.code_interpreter_cog = code_interpreter_cog
        self.ctx = ctx
        self.session = session
        self.artifact_names = artifact_names
        self.add_item(
            DownloadButton(
                self.ctx, self.code_interpreter_cog, self.session, self.artifact_names
            )
        )


# A view for a follow-up button
class DownloadButton(discord.ui.Button["CodeInterpreterDownloadArtifactsView"]):
    def __init__(self, ctx, code_interpreter_cog, session, artifact_names):
        super().__init__(label="Download Artifacts", style=discord.ButtonStyle.gray)
        self.code_interpreter_cog = code_interpreter_cog
        self.ctx = ctx
        self.session = session
        self.artifact_names = artifact_names

    async def callback(self, interaction: discord.Interaction):
        """Send the followup modal"""
        await interaction.response.send_message(
            "Downloading the artifacts: "
            + str(self.artifact_names)
            + ". This may take a while.",
            ephemeral=True,
            delete_after=120,
        )
        for artifact in self.artifact_names:
            try:
                runner = functools.partial(
                    self.session.download_file, filepath=artifact
                )

                bytes = await asyncio.get_running_loop().run_in_executor(None, runner)
                # Save these bytes into a tempfile
                with tempfile.NamedTemporaryFile(delete=False) as temp:
                    temp.write(bytes)
                    temp.flush()
                    temp.seek(0)
                    await self.ctx.channel.send(
                        file=discord.File(temp.name, filename=artifact)
                    )
                    os.unlink(temp.name)
            except:
                traceback.print_exc()
                await self.ctx.channel.send(
                    "Failed to download artifact: " + artifact, delete_after=120
                )
