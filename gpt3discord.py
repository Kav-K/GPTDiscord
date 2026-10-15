"""GPTDiscord entry point. Importing this module does not start the bot."""

import asyncio
import importlib
import logging
import os
import signal
import sys
from pathlib import Path
from platform import system

from services.environment_service import EnvService

__version__ = "13.0.0"

PID_FILE = Path("bot.pid")
PROCESS = None
logger = logging.getLogger(__name__)


def load_optional_service(module_name, class_name, extra):
    """Explain missing optional packages at startup, before connecting to Discord."""
    try:
        return getattr(importlib.import_module(module_name), class_name)
    except ImportError as exc:
        raise RuntimeError(
            f"Cannot load {class_name}. Install its dependencies with "
            f"pip install 'gpt3discord[{extra}]' and check the integration configuration."
        ) from exc


async def main():
    # Validate before importing cogs: several initialize their local SQLite tables.
    if not os.getenv("DISCORD_TOKEN"):
        raise ValueError("Set DISCORD_TOKEN to your Discord application's bot token.")
    if not EnvService.get_openai_token() and not EnvService.get_user_input_api_keys():
        raise ValueError("Set OPENAI_API_KEY (or the legacy OPENAI_TOKEN).")
    EnvService.get_allowed_guilds()
    data_path = EnvService.environment_path_with_fallback("DATA_DIR")
    data_path.mkdir(parents=True, exist_ok=True)
    (EnvService.save_path() / "pickles").mkdir(parents=True, exist_ok=True)

    import discord
    from pycord.multicog import Bot

    from cogs.commands import Commands
    from cogs.image_service_cog import DrawDallEService
    from cogs.moderations_service_cog import ModerationsService
    from cogs.prompt_optimizer_cog import ImgPromptOptimizer
    from cogs.text_service_cog import GPT3ComCon
    from cogs.transcription_service_cog import TranscribeService
    from cogs.web_search_cog import WebSearchCog
    from models.openai_model import Model
    from services.deletion_service import Deletion
    from services.message_queue_service import Message
    from services.pickle_service import Pickler
    from services.usage_service import UsageService

    activity = discord.Activity(
        type=discord.ActivityType.watching, name="for /help, /gpt, and /web"
    )
    bot = Bot(intents=discord.Intents.all(), command_prefix="!", activity=activity)
    usage_service = UsageService(data_path)
    model = Model(usage_service)
    message_queue, deletion_queue, pickle_queue = (
        asyncio.Queue(),
        asyncio.Queue(),
        asyncio.Queue(),
    )

    pinecone_service = None
    if os.getenv("PINECONE_TOKEN"):
        PineconeService = load_optional_service(
            "services.pinecone_service", "PineconeService", "pinecone"
        )
        pinecone_service = PineconeService.from_config(
            os.environ["PINECONE_TOKEN"], EnvService.get_pinecone_region()
        )

    @bot.event
    async def on_ready():
        logger.info("Logged in as %s", bot.user)

    @bot.event
    async def on_application_command_error(ctx, error):
        if isinstance(error, discord.CheckFailure):
            return
        logger.error("Application command failed", exc_info=error)
        await ctx.respond(
            "The command failed. Check the bot logs for details.", ephemeral=True
        )

    bot.add_cog(ModerationsService(bot, usage_service, model))
    bot.add_cog(
        GPT3ComCon(
            bot,
            usage_service,
            model,
            message_queue,
            deletion_queue,
            int(os.getenv("DEBUG_GUILD") or 0),
            int(os.getenv("DEBUG_CHANNEL") or 0),
            data_path,
            pinecone_service=pinecone_service,
            pickle_queue=pickle_queue,
        )
    )
    bot.add_cog(
        DrawDallEService(
            bot,
            usage_service,
            model,
            message_queue,
            deletion_queue,
            bot.get_cog("GPT3ComCon"),
        )
    )
    bot.add_cog(
        ImgPromptOptimizer(
            bot,
            usage_service,
            model,
            message_queue,
            deletion_queue,
            bot.get_cog("GPT3ComCon"),
            bot.get_cog("DrawDallEService"),
        )
    )
    bot.add_cog(WebSearchCog(bot, usage_service=usage_service))

    if EnvService.get_indexing_enabled():
        IndexService = load_optional_service(
            "cogs.index_service_cog", "IndexService", "index"
        )
        bot.add_cog(IndexService(bot, usage_service, deletion_queue))

    if EnvService.get_deepl_token():
        from cogs.translation_service_cog import TranslationService
        from models.deepl_model import TranslationModel

        bot.add_cog(TranslationService(bot, TranslationModel()))

    if (
        EnvService.get_google_search_api_key()
        and EnvService.get_google_search_engine_id()
    ):
        SearchService = load_optional_service(
            "cogs.search_service_cog", "SearchService", "search"
        )
        bot.add_cog(
            SearchService(
                bot, model, usage_service, deletion_queue, bot.get_cog("GPT3ComCon")
            )
        )

    if EnvService.get_e2b_api_key():
        CodeInterpreterService = load_optional_service(
            "cogs.code_interpreter_service_cog", "CodeInterpreterService", "code"
        )
        bot.add_cog(
            CodeInterpreterService(
                bot, model, usage_service, deletion_queue, bot.get_cog("GPT3ComCon")
            )
        )

    bot.add_cog(TranscribeService(bot, model, usage_service))
    bot.add_cog(
        Commands(
            bot,
            usage_service,
            model,
            message_queue,
            deletion_queue,
            bot.get_cog("GPT3ComCon"),
            bot.get_cog("DrawDallEService"),
            bot.get_cog("ImgPromptOptimizer"),
            bot.get_cog("ModerationsService"),
            bot.get_cog("IndexService"),
            bot.get_cog("TranslationService"),
            bot.get_cog("SearchService"),
            bot.get_cog("TranscribeService"),
            bot.get_cog("CodeInterpreterService"),
        )
    )

    workers = [
        asyncio.create_task(Message.process_message_queue(message_queue, 1.5, 5)),
        asyncio.create_task(Deletion.process_deletion_queue(deletion_queue, 1, 1)),
        asyncio.create_task(Pickler.process_pickle_queue(pickle_queue, 5, 1)),
    ]
    try:
        await bot.start(os.environ["DISCORD_TOKEN"])
    finally:
        await bot.close()
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)


def _windows_process_running(pid):
    """Query a Windows process without sending console events or terminating it."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(
        0x1000, False, pid
    )  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        # An inaccessible process can still be running; do not start a duplicate.
        return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def check_process_file(pid_file: Path) -> bool:
    """Reject an active PID; remove malformed and stale PID files."""
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
        if pid <= 0:
            raise ValueError("Invalid PID")
    except ValueError:
        pid_file.unlink(missing_ok=True)
        return False
    if system() == "Linux":
        if (Path("/proc") / str(pid)).exists():
            return True
        pid_file.unlink(missing_ok=True)
        return False
    if system() == "Windows":
        if _windows_process_running(pid):
            return True
        pid_file.unlink(missing_ok=True)
        return False
    # Unix supports a non-signalling existence check. Windows os.kill(pid, 0)
    # sends a console event, so it must never be used as a Windows PID probe.
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        pid_file.unlink(missing_ok=True)
        return False
    except PermissionError:
        return True
    return True


def cleanup_pid_file(signum=None, frame=None):
    if PROCESS:
        PROCESS.terminate()
        PROCESS.join(timeout=5)
    PID_FILE.unlink(missing_ok=True)


def _handle_sigterm(signum, frame):
    raise KeyboardInterrupt


def init():
    global PROCESS
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    if check_process_file(PID_FILE):
        logger.error("Another bot process is running (see %s).", PID_FILE)
        return 1
    PID_FILE.write_text(str(os.getpid()))
    # asyncio.run handles SIGINT; convert SIGTERM to the same orderly shutdown.
    previous_handler = signal.signal(signal.SIGTERM, _handle_sigterm)
    try:
        if EnvService.get_health_service_enabled():
            from services.health_service import HealthService

            PROCESS = HealthService().get_process()
        asyncio.run(main())
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        logger.exception("Bot startup or execution failed")
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
        cleanup_pid_file()


if __name__ == "__main__":
    sys.exit(init())
