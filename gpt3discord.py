import os
import asyncio
import sys
import threading
import traceback
from pathlib import Path

import discord
import pinecone
from pycord.multicog import apply_multicog

from cogs.text_service_cog import GPT3ComCon
from cogs.image_service_cog import DrawDallEService
from cogs.prompt_optimizer_cog import ImgPromptOptimizer
from cogs.moderations_service_cog import ModerationsService
from cogs.commands import Commands
from cogs.translation_service_cog import TranslationService
from models.deepl_model import TranslationModel
from services.health_service import HealthService

from services.pinecone_service import PineconeService
from services.deletion_service import Deletion
from services.message_queue_service import Message
from services.usage_service import UsageService
from services.environment_service import EnvService

from models.openai_model import Model


__version__ = "8.1.3"


if sys.platform == "win32":
    separator = "\\"
else:
    separator = "/"

#
# The pinecone service is used to store and retrieve conversation embeddings.
#

try:
    PINECONE_TOKEN = os.getenv("PINECONE_TOKEN")
except Exception:
    PINECONE_TOKEN = None

pinecone_service = None
if PINECONE_TOKEN:
    pinecone.init(api_key=PINECONE_TOKEN, environment="us-west1-gcp")
    PINECONE_INDEX = "conversation-embeddings"
    if PINECONE_INDEX not in pinecone.list_indexes():
        print("Creating pinecone index. Please wait...")
        pinecone.create_index(
            "conversation-embeddings",
            dimension=1536,
            metric="dotproduct",
            pod_type="s1",
        )

    pinecone_service = PineconeService(pinecone.Index(PINECONE_INDEX))
    print("Got the pinecone service")

#
# Message queueing for the debug service, defer debug messages to be sent later so we don't hit rate limits.
#
message_queue = asyncio.Queue()
deletion_queue = asyncio.Queue()
asyncio.ensure_future(Message.process_message_queue(message_queue, 1.5, 5))
asyncio.ensure_future(Deletion.process_deletion_queue(deletion_queue, 1, 1))


#
# Settings for the bot
#
activity = discord.Activity(
    type=discord.ActivityType.watching, name="for /help /gpt, and more!"
)
bot = discord.Bot(intents=discord.Intents.all(), command_prefix="!", activity=activity)
usage_service = UsageService(Path(os.environ.get("DATA_DIR", os.getcwd())))
model = Model(usage_service)


#
# An encapsulating wrapper for the discord.py client. This uses the old re-write without cogs, but it gets the job done!
#


@bot.event  # Using self gives u
async def on_ready():  # I can make self optional by
    print("We have logged in as {0.user}".format(bot))


@bot.event
async def on_application_command_error(
    ctx: discord.ApplicationContext, error: discord.DiscordException
):
    if isinstance(error, discord.CheckFailure):
        pass
    else:
        raise error


async def main():
    data_path = EnvService.environment_path_with_fallback("DATA_DIR")
    debug_guild = int(os.getenv("DEBUG_GUILD"))
    debug_channel = int(os.getenv("DEBUG_CHANNEL"))

    if not data_path.exists():
        raise OSError(f"Data path: {data_path} does not exist ... create it?")

    # Load the cog for the moderations service
    bot.add_cog(ModerationsService(bot, usage_service, model))

    # Load the main GPT3 Bot service
    bot.add_cog(
        GPT3ComCon(
            bot,
            usage_service,
            model,
            message_queue,
            deletion_queue,
            debug_guild,
            debug_channel,
            data_path,
            pinecone_service=pinecone_service,
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

    if EnvService.get_deepl_token():
        bot.add_cog(TranslationService(bot, TranslationModel()))
        print("The translation service is enabled.")

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
            bot.get_cog("TranslationService"),
        )
    )

    apply_multicog(bot)

    await bot.start(os.getenv("DISCORD_TOKEN"))


# Run the bot with a token taken from an environment file.
def init():
    PID_FILE = "bot.pid"
    process = None
    if os.path.exists(PID_FILE):
        print("Process ID file already exists")
        sys.exit(1)
    else:
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))
            print("" "Wrote PID to f" "ile the file " + PID_FILE)
            f.close()
    try:
        if EnvService.get_health_service_enabled():
            try:
                process = HealthService().get_process()
            except:
                traceback.print_exc()
                print("The health service failed to start.")

        asyncio.get_event_loop().run_until_complete(main())
    except KeyboardInterrupt:
        print("Caught keyboard interrupt, killing and removing PID")
        os.remove(PID_FILE)
    except Exception as e:
        traceback.print_exc()
        print(str(e))
        print("Removing PID file")
        os.remove(PID_FILE)
    finally:
        # Kill all threads
        if process:
            print("Killing all subprocesses")
            process.terminate()
        print("Killed all subprocesses")
        sys.exit(0)


if __name__ == "__main__":
    sys.exit(init())
