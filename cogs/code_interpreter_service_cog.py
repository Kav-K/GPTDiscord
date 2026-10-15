import asyncio
import base64
import datetime
import functools
import io
import posixpath
import shlex
import traceback
import uuid

import discord
from e2b_code_interpreter import Sandbox

from langchain_community.utilities.google_search import GoogleSearchAPIWrapper
from langchain_core.tools import Tool

from models.embed_statics_model import EmbedStatics
from services.agent_service import agent_output_text, create_chat_agent, create_chat_llm
from services.deletion_service import Deletion
from services.environment_service import EnvService
from utils.safe_ctx_respond import safe_ctx_respond, safe_remove_list


PRE_MODERATE = EnvService.get_premoderate()
USER_INPUT_API_KEYS = EnvService.get_user_input_api_keys()
USER_KEY_DB = EnvService.get_api_db()
GOOGLE_API_KEY = EnvService.get_google_search_api_key()
GOOGLE_SEARCH_ENGINE_ID = EnvService.get_google_search_engine_id()


class CodeInterpreterService(discord.Cog, name="CodeInterpreterService"):
    """Stateful Python execution in E2B sandboxes."""

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
        self.thread_owners = {}
        self.thread_awaiting_responses = []
        self.converser_cog = converser_cog
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

        if message.channel.id not in self.chat_agents:
            return

        if USER_INPUT_API_KEYS and self.thread_owners.get(message.channel.id) != message.author.id:
            await message.reply("This session uses its creator's API key. Start your own code interpreter session to participate.")
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
            from services.moderations_service import Moderation

            if await Moderation.simple_moderate_and_respond(message.content, message):
                await message.delete()
                return

        prompt = message.content.strip()

        # If the message channel is in self.chat_agents, then we delegate the message to the agent.
        if message.channel.id in self.chat_agents:
            if prompt.lower() in ["stop", "end", "quit", "exit"]:
                session = self.sessions.pop(message.channel.id)
                await asyncio.to_thread(session.close)
                await message.reply(
                    "Code interpreter session ended. Its sandbox and files have been removed."
                )
                self.chat_agents.pop(message.channel.id)
                self.thread_owners.pop(message.channel.id, None)

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
                    title="🤖💬 Uploading file to code interpreter environment...",
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

                try:
                    filename = posixpath.basename(file.filename)
                    uploaded_path = await self.sessions[
                        message.channel.id
                    ].upload_file_async(filename, await file.read())
                    prompt += (
                        f"\n{{The user has just uploaded a file to {uploaded_path}}}"
                    )
                except Exception:
                    traceback.print_exc()
                    await message.reply(
                        embed=discord.Embed(
                            title="🤖💬 File upload failed", color=0x808080
                        )
                    )
                    return
                finally:
                    if "thinking_message" in locals():
                        try:
                            await thinking_message.delete()
                        except discord.HTTPException:
                            pass

            self.thread_awaiting_responses.append(message.channel.id)

            try:
                await message.channel.trigger_typing()
            except:
                pass

            agent = self.chat_agents[message.channel.id]
            try:
                self.usage_service.update_usage_memory(
                    message.guild.name, "code_interpreter_message", 1
                )
                result = await asyncio.to_thread(agent.invoke, {"input": prompt})
                response = agent_output_text(result["output"])
                artifact_names = await asyncio.to_thread(
                    self.sessions[message.channel.id].get_artifact_names
                )

            except Exception as e:
                response = f"Error: {e}"
                traceback.print_exc()
                await message.reply(
                    embed=EmbedStatics.get_code_chat_failure_embed(response)
                )
                safe_remove_list(self.thread_awaiting_responses, message.channel.id)
                return

            artifacts_available = len(artifact_names) > 0

            if len(response) > 2000:
                embed_pages = EmbedStatics.paginate_chat_embed(response)

                for x, page in enumerate(embed_pages):
                    if x == 0:
                        previous_message = await message.reply(embed=page)
                    else:
                        previous_message = await previous_message.reply(embed=page)

                if artifacts_available:
                    await previous_message.reply(
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
                    view=(
                        CodeInterpreterDownloadArtifactsView(
                            message,
                            self,
                            self.sessions[message.channel.id],
                            artifact_names,
                        )
                        if artifacts_available
                        else None
                    ),
                )

            safe_remove_list(self.thread_awaiting_responses, message.channel.id)

    class SessionedCodeExecutor:
        ARTIFACT_DIRECTORY = "/home/user/artifacts"

        def __init__(self):
            self.session = None
            self.sessioned = False
            try:
                self.session = Sandbox.create(
                    api_key=EnvService.get_e2b_api_key(), timeout=3600
                )
                self.session.files.make_dir(self.ARTIFACT_DIRECTORY)
                self.sessioned = True
            except Exception:
                traceback.print_exc()
                if self.session is not None:
                    self.session.kill()

        def execute_code_sync(self, code: str):
            """Run Python in the session's persistent Jupyter kernel."""
            execution = self.session.run_code(code, timeout=30)
            stdout = "".join(execution.logs.stdout)
            stderr = "".join(execution.logs.stderr)

            # Rich Jupyter results replace the retired DataAnalysis artifacts API.
            # Keep plots downloadable alongside files written by user code.
            for result in execution.results:
                if result.text:
                    stdout += "\n" + result.text
                for extension in ("png", "jpeg", "pdf", "svg", "html"):
                    content = getattr(result, extension, None)
                    if content:
                        if extension in ("png", "jpeg", "pdf"):
                            content = base64.b64decode(content)
                        path = (
                            f"{self.ARTIFACT_DIRECTORY}/{uuid.uuid4().hex}.{extension}"
                        )
                        self.session.files.write(path, content)

            if execution.error:
                stderr += (
                    f"\n{execution.error.name}: {execution.error.value}\n"
                    + execution.error.traceback
                )
            artifacts = self.get_artifact_names()
            artifacts_output = (
                "\nArtifacts: " + str(artifacts) if artifacts else "\nNO__ARTIFACTS"
            )
            return (
                f"STDOUT: {stdout[:12000]}\nSTDERR: {stderr[:12000]}{artifacts_output}"
            )

        async def execute_code_async(self, code: str):
            return await asyncio.to_thread(self.execute_code_sync, code)

        def get_artifact_names(self):
            entries = self.session.files.list(self.ARTIFACT_DIRECTORY, depth=10)
            return [
                entry.path
                for entry in entries
                if getattr(entry.type, "value", entry.type) == "file"
            ]

        def close(self):
            self.session.kill()
            self.sessioned = False

        def download_file(self, filepath):
            return bytes(
                self.session.files.read(filepath, format="bytes", request_timeout=30)
            )

        def install_python_package(self, package):
            return self.run_command_sync(
                f"python -m pip install -- {shlex.quote(package)}"
            )

        def install_system_package(self, package):
            result = self.session.commands.run(
                f"apt-get update && apt-get install -y -- {shlex.quote(package)}",
                user="root",
                timeout=120,
            )
            return f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"

        def run_command_sync(self, command):
            result = self.session.commands.run(command, timeout=120, cwd="/home/user")
            return f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"

        async def run_command_async(self, command):
            return await asyncio.to_thread(self.run_command_sync, command)

        def is_sessioned(self):
            return self.sessioned

        def upload_file_sync(self, path, file):
            # Discord attachment names must remain within the user's directory.
            filename = posixpath.basename(path)
            if filename in ("", ".", ".."):
                raise ValueError("A filename is required")
            destination = f"/home/user/{filename}"
            self.session.files.write(destination, file)
            return destination

        async def upload_file_async(self, path, file):
            return await asyncio.to_thread(self.upload_file_sync, path, file)

    async def code_interpreter_chat_command(
        self,
        ctx: discord.ApplicationContext,
        model,
        temperature,
        top_p,
    ):
        await ctx.defer()
        user_api_key = None
        if USER_INPUT_API_KEYS:
            from services.text_service import TextService

            user_api_key = await TextService.get_user_api_key(
                ctx.user.id, ctx, USER_KEY_DB
            )
            if not user_api_key:
                return

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
        await safe_ctx_respond(ctx=ctx, content="Conversation started.")

        self.sessions[thread.id] = await asyncio.to_thread(self.SessionedCodeExecutor)

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
                description="This tool is able to execute Python 3 code. The input to the tool is just the raw python code. The output is the stdout of the code. When using the output of the code execution tool, always make sure to always display the raw output to the user as well.",
            ),
            Tool(
                name="Install-python-package-tool",
                func=self.sessions[thread.id].install_python_package,
                description="This tool installs a python package into the execution environment. The input to the tool is a single python package name (e.g 'numpy'). If you need to install multiple python packages, call this tool multiple times.",
            ),
            Tool(
                name="Install-system-package-tool",
                func=self.sessions[thread.id].install_system_package,
                description="This tool installs a system package into the system environment. The input to the tool is a single package name (e.g 'htop'). If you need to install multiple system packages, call this tool multiple times.",
            ),
            Tool(
                name="Run-command-tool",
                func=self.sessions[thread.id].run_command_sync,
                description="This tool allows you to run terminal (bash/unix) commands in the execution environment. The input to the tool is the command to run. An example input can be 'df -h'",
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

        llm = create_chat_llm(
            model=model,
            temperature=temperature,
            top_p=top_p,
            api_key=user_api_key,
        )
        system_message = (
            "You are an expert programmer that is able to use the tools to your advantage to execute "
            "python code. Help the user iterate on their code and test it through execution. "
            "Always provide the full code output when asked for "
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
        )
        self.chat_agents[thread.id] = create_chat_agent(llm, tools, system_message)
        self.thread_owners[thread.id] = ctx.user.id


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

                content = await asyncio.get_running_loop().run_in_executor(None, runner)
                await self.ctx.channel.send(
                    file=discord.File(
                        io.BytesIO(content), filename=posixpath.basename(artifact)
                    )
                )
            except:
                traceback.print_exc()
                await self.ctx.channel.send(
                    "Failed to download artifact: " + artifact, delete_after=120
                )
