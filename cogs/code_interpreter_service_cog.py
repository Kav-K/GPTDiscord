import asyncio
import base64
import datetime
import functools
import io
import posixpath
import shlex
import traceback
import threading
import uuid

import discord
from e2b import CommandExitException
from e2b_code_interpreter import Sandbox

from langchain_community.utilities.google_search import GoogleSearchAPIWrapper
from langchain_core.tools import Tool

from models.embed_statics_model import EmbedStatics
from services.agent_service import (
    agent_output_text, create_chat_agent, create_chat_llm,
    personal_key_fingerprint, session_key_is_current,
)
from services.deletion_service import Deletion
from services.environment_service import EnvService
from services.access_service import message_has_access
from services.discord_attachment_service import read_attachment_bytes
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
        self.thread_key_fingerprints = {}
        self.thread_awaiting_responses = []
        self.converser_cog = converser_cog
        self.recovery = getattr(converser_cog, "conversation_recovery", None)
        if self.recovery is not None:
            self.recovery.register_route(
                "code",
                channels=lambda: self.chat_agents,
                owner_id=lambda channel_id: self.thread_owners.get(channel_id),
                handler=self._process_message,
            )
        self.initial_messages = {}
        self.sessions = {}
        self._sandbox_tasks = set()
        self._startup_tasks = set()
        self._starting_threads = set()
        self._closing = False
        # Make a mapping of all the country codes and their full country names:

    @discord.Cog.listener()
    async def on_message(self, message):
        if not await message_has_access(message):
            return
        if getattr(getattr(message, "channel", None), "id", None) in self._starting_threads:
            await self._process_message(message)
        elif self.recovery is not None:
            await self.recovery.dispatch(message, route="code")
        else:
            await self._process_message(message)

    async def _process_message(self, message):
        if not await message_has_access(message):
            return
        if self._closing:
            return
        # Check if the message is from a bot.
        if getattr(message.author, "bot", False):
            return

        # Check if the message is from a guild.
        if not message.guild:
            return

        # System message
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return

        if message.content.strip().startswith("~"):
            return

        if message.channel.id in self._starting_threads:
            await message.reply(
                "The code interpreter sandbox is still starting. Please wait for the ready message, then send your request again.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        if message.channel.id not in self.chat_agents:
            return

        if (
            USER_INPUT_API_KEYS
            and self.thread_owners.get(message.channel.id) != message.author.id
        ):
            await message.reply(
                "This session uses its creator's API key. Start your own code interpreter session to participate."
            )
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

        self.thread_awaiting_responses.append(message.channel.id)
        try:
            await self._process_session_message(message)
        finally:
            safe_remove_list(self.thread_awaiting_responses, message.channel.id)

    async def _process_session_message(self, message):
        ending = message.content.strip().lower() in {"stop", "end", "quit", "exit"}
        if USER_INPUT_API_KEYS and not ending and not await session_key_is_current(
            message, self.thread_key_fingerprints.get(message.channel.id), USER_KEY_DB
        ):
            return
        # Ending a session remains possible after its key has been revoked.
        if PRE_MODERATE and not ending:
            from services.moderations_service import Moderation

            if await Moderation.simple_moderate_and_respond(message.content, message):
                await message.delete()
                return

        prompt = message.content.strip()

        # If the message channel is in self.chat_agents, then we delegate the message to the agent.
        if message.channel.id in self.chat_agents:
            if prompt.lower() in ["stop", "end", "quit", "exit"]:
                session = self.sessions[message.channel.id]
                try:
                    await asyncio.to_thread(session.close)
                except Exception:
                    print("Code interpreter sandbox cleanup failed.")
                self.sessions.pop(message.channel.id, None)
                self.chat_agents.pop(message.channel.id, None)
                self.thread_owners.pop(message.channel.id, None)
                self.thread_key_fingerprints.pop(message.channel.id, None)
                await message.reply("Code interpreter session ended.")

                # close the thread
                thread = message.channel
                await thread.edit(name="Closed-GPT")
                await thread.edit(archived=True)
                return

            for file in message.attachments:
                thinking_message = None
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
                    ].upload_file_async(filename, await read_attachment_bytes(file))
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
                    if thinking_message is not None:
                        try:
                            await thinking_message.delete()
                        except discord.HTTPException:
                            pass

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

            except Exception:
                print("Code interpreter request failed.")
                await message.reply(
                    embed=EmbedStatics.get_code_chat_failure_embed(
                        "The code interpreter request failed. Please try again or start a new session."
                    )
                )
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

    class SessionedCodeExecutor:
        ARTIFACT_DIRECTORY = "/home/user/artifacts"

        def __init__(self):
            self.session = None
            self.sessioned = False
            self._close_lock = threading.Lock()
            try:
                self.session = Sandbox.create(
                    api_key=EnvService.get_e2b_api_key(), timeout=3600,
                    request_timeout=30, retries=0
                )
                self.session.files.make_dir(self.ARTIFACT_DIRECTORY, request_timeout=30)
                self.sessioned = True
            except Exception:
                print("Code interpreter sandbox initialization failed.")
                if self.session is not None:
                    try:
                        self.session.kill(request_timeout=30, retries=0)
                    except Exception:
                        print("Code interpreter sandbox cleanup failed.")

        def execute_code_sync(self, code: str):
            """Run Python in the session's persistent Jupyter kernel."""
            execution = self.session.run_code(code, timeout=30, request_timeout=45)
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
                        self.session.files.write(path, content, request_timeout=30)

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
            entries = self.session.files.list(self.ARTIFACT_DIRECTORY, depth=10, request_timeout=30)
            return [
                entry.path
                for entry in entries
                if getattr(entry.type, "value", entry.type) == "file"
            ]

        def close(self):
            with self._close_lock:
                if not self.sessioned:
                    return
                self.sessioned = False
                self.session.kill(request_timeout=30, retries=0)

        def download_file(self, filepath, max_bytes=None):
            path = posixpath.normpath(filepath)
            if not path.startswith(self.ARTIFACT_DIRECTORY + "/"):
                raise ValueError("Only files in the artifacts folder can be downloaded.")
            if max_bytes is not None:
                info = self.session.files.get_info(path, request_timeout=30)
                if info.size > max_bytes:
                    raise ValueError("This artifact exceeds the server's upload limit.")
            content = bytearray()
            with self.session.files.read(path, format="stream", request_timeout=30) as stream:
                for chunk in stream:
                    if max_bytes is not None and len(content) + len(chunk) > max_bytes:
                        raise ValueError("This artifact exceeds the server's upload limit.")
                    content.extend(chunk)
            return bytes(content)

        def install_python_package(self, package):
            return self.run_command_sync(
                f"python -m pip install -- {shlex.quote(package)}"
            )

        def install_system_package(self, package):
            return self._run_command(
                f"apt-get update && apt-get install -y -- {shlex.quote(package)}",
                user="root",
            )

        def _run_command(self, command, **kwargs):
            try:
                result = self.session.commands.run(command, timeout=120, **kwargs)
            except CommandExitException as error:
                # Nonzero exits are tool output. Returning diagnostics lets the
                # agent correct a command or package name in the same turn.
                result = error
            return f"EXIT CODE: {result.exit_code}\nSTDOUT: {result.stdout[:12000]}\nSTDERR: {result.stderr[:12000]}"

        def run_command_sync(self, command):
            return self._run_command(command, cwd="/home/user")

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
            self.session.files.write(destination, file, request_timeout=30)
            return destination

        async def upload_file_async(self, path, file):
            return await asyncio.to_thread(self.upload_file_sync, path, file)

    async def _close_sandbox(self, session):
        if session is None or not session.is_sessioned():
            return
        try:
            await asyncio.wait_for(asyncio.to_thread(session.close), timeout=30)
        except Exception:
            print("Code interpreter sandbox cleanup failed.")

    def _close_late_sandbox(self, creation):
        try:
            session = creation.result()
        except BaseException:
            return
        if session is None or not session.is_sessioned():
            return
        cleanup = asyncio.create_task(self._close_sandbox(session))
        self._sandbox_tasks.add(cleanup)
        cleanup.add_done_callback(self._sandbox_tasks.discard)

    async def close_sessions(self):
        self._closing = True
        current = asyncio.current_task()
        startups = {task for task in self._startup_tasks if task is not current}
        for task in startups:
            task.cancel()
        sessions = [session for thread_id, session in self.sessions.items() if thread_id not in self._starting_threads]
        self.sessions.clear()
        self.chat_agents.clear()
        self.thread_owners.clear()
        self.thread_key_fingerprints.clear()
        for session in sessions:
            cleanup = asyncio.create_task(self._close_sandbox(session))
            self._sandbox_tasks.add(cleanup)
            cleanup.add_done_callback(self._sandbox_tasks.discard)
        deadline = asyncio.get_running_loop().time() + 30
        while True:
            tasks = {task for task in startups | self._sandbox_tasks if not task.done()}
            remaining = deadline - asyncio.get_running_loop().time()
            if not tasks or remaining <= 0:
                return
            await asyncio.wait(tasks, timeout=remaining)
            await asyncio.sleep(0)

    async def code_interpreter_chat_command(
        self,
        ctx: discord.ApplicationContext,
        model,
        temperature,
        top_p,
    ):
        user_api_key = None
        if USER_INPUT_API_KEYS:
            from services.text_service import TextService

            user_api_key = await TextService.get_user_api_key(
                ctx.user.id, ctx, USER_KEY_DB
            )
            if not user_api_key:
                return

        await ctx.defer()
        if self._closing:
            await ctx.respond("The bot is shutting down. Please try again after it reconnects.", ephemeral=True)
            return
        guild_id = getattr(ctx, "guild_id", None)
        if self.recovery is not None and self.bot.get_guild(guild_id) is None:
            prepared = False
            try:
                if self.recovery.enabled_for(guild_id):
                    prepared = await self.recovery.prepare_context(ctx)
            except Exception:
                print("Optional chat REST setup failed.")
            if not prepared:
                await ctx.respond(
                    "Discord has not delivered this server's events. Ask the bot operator to check scoped REST recovery before starting this chat.",
                    ephemeral=True,
                )
                return
        embed_title = f"{ctx.user.name}'s code interpreter conversation with GPT"
        message_embed = discord.Embed(
            title=embed_title,
            description=f"Preparing your code session. Wait for the ready message before sending requests.\nModel: {model}\n\nType `end` to stop the conversation",
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
        if self._closing:
            await thread.edit(name="Closed-GPT", archived=True)
            return
        startup = asyncio.current_task()
        self._startup_tasks.add(startup)
        self._starting_threads.add(thread.id)
        abandoned = threading.Event()

        def create_sandbox():
            if abandoned.is_set() or self._closing:
                return None
            session = self.SessionedCodeExecutor()
            # The event-loop can finish before this worker. Close a late sandbox
            # here as well, so cleanup does not depend on a running task callback.
            if abandoned.is_set() or self._closing:
                if session.is_sessioned():
                    session.close()
                return None
            return session

        creation = asyncio.create_task(asyncio.to_thread(create_sandbox))
        self._sandbox_tasks.add(creation)
        creation.add_done_callback(self._sandbox_tasks.discard)
        try:
            # A cancelled command cannot cancel or discard a creation still
            # running in its worker thread; its result must remain recoverable.
            self.sessions[thread.id] = await asyncio.shield(creation)

            if not self.sessions[thread.id].is_sessioned():
                raise RuntimeError("Code interpreter sandbox is unavailable")

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
                store=False,
                max_tokens=4096,
                output_version="responses/v1",
            )
            system_message = (
                "Help the user analyze data, write and run code in a persistent Linux sandbox. "
                "Use the code execution tool when computation or verification helps with their request. "
                "Python variables and installed packages persist for this session. "
                "User uploads are saved under /home/user; use the exact paths supplied with their messages. "
                "Save files the user should download under /home/user/artifacts, including reports and plots. "
                "The Python execution tool uses a persistent Jupyter kernel; the command tool runs shell commands. "
                "Install Python and system packages with their respective tools, one package per call. "
                "Check tool exit codes and stderr, correct errors when possible, and report failures honestly. "
                "Show useful code and its actual execution output in your answer. "
                "Do not invent execution results or artifact paths."
            )
            self.chat_agents[thread.id] = create_chat_agent(llm, tools, system_message)
            self.thread_owners[thread.id] = ctx.user.id
            if USER_INPUT_API_KEYS:
                self.thread_key_fingerprints[thread.id] = personal_key_fingerprint(user_api_key)
            if self.recovery is not None:
                await self.recovery.watch_thread(thread, new=True)
            self._starting_threads.discard(thread.id)
            await thread.send(
                "Code interpreter is ready. Send a message or upload files to begin; type `end` to close the sandbox.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            await safe_ctx_respond(ctx=ctx, content="Conversation started.")
        except BaseException as error:
            abandoned.set()
            self.chat_agents.pop(thread.id, None)
            self.thread_owners.pop(thread.id, None)
            self.thread_key_fingerprints.pop(thread.id, None)
            session = self.sessions.pop(thread.id, None)
            if session is None:
                # Even if creation finishes immediately after cancellation,
                # callbacks run for completed tasks and retrieve that result.
                creation.add_done_callback(self._close_late_sandbox)
            else:
                await self._close_sandbox(session)
            try:
                if isinstance(error, Exception):
                    print("Code interpreter session startup failed.")
                    await thread.send(
                        "Failed to start code interpreter session. Check the E2B and OpenAI configuration, then try again."
                    )
                await thread.edit(name="Closed-GPT (Error)", archived=True)
            except Exception:
                print("Code interpreter failed thread cleanup.")
            if not isinstance(error, Exception):
                raise
        finally:
            self._startup_tasks.discard(startup)
            self._starting_threads.discard(thread.id)


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
        if (
            USER_INPUT_API_KEYS
            and self.code_interpreter_cog.thread_owners.get(self.ctx.channel.id)
            != interaction.user.id
        ):
            await interaction.response.send_message(
                "Only this session's creator can download its artifacts.",
                ephemeral=True,
            )
            return
        if (
            self.code_interpreter_cog.sessions.get(self.ctx.channel.id) is not self.session
            or not self.session.is_sessioned()
        ):
            await interaction.response.send_message(
                "This code interpreter session has ended. Start a new /code chat to create more artifacts.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            "Downloading available artifacts.", ephemeral=True, delete_after=120,
        )
        limit = getattr(getattr(self.ctx, "guild", None), "filesize_limit", 10 * 1024 * 1024)
        for artifact in self.artifact_names:
            try:
                runner = functools.partial(self.session.download_file, filepath=artifact, max_bytes=limit)
                content = await asyncio.to_thread(runner)
                await self.ctx.channel.send(
                    file=discord.File(io.BytesIO(content), filename=posixpath.basename(artifact)),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except ValueError as error:
                await interaction.followup.send(str(error), ephemeral=True)
            except Exception:
                print("Code interpreter artifact download failed.")
                await interaction.followup.send(
                    "The artifact could not be downloaded. Try again or start a new session if it has expired.",
                    ephemeral=True,
                )
