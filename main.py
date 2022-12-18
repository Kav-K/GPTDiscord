import asyncio
import sys

import discord
from discord.ext import commands
from dotenv import load_dotenv

from cogs.gpt_3_commands_and_converser import GPT3ComCon
from cogs.image_prompt_optimizer import ImgPromptOptimizer
from models.deletion_service import Deletion
from models.message_model import Message
from models.openai_model import Model
from models.usage_service_model import UsageService

load_dotenv()
import os

"""
Message queueing for the debug service, defer debug messages to be sent later so we don't hit rate limits.
"""
message_queue = asyncio.Queue()
deletion_queue = asyncio.Queue()
asyncio.ensure_future(Message.process_message_queue(message_queue, 1.5, 5))
asyncio.ensure_future(Deletion.process_deletion_queue(deletion_queue, 1, 1))


"""
Settings for the bot
"""
bot = commands.Bot(intents=discord.Intents.all(), command_prefix="!")
usage_service = UsageService()
model = Model(usage_service)


"""
An encapsulating wrapper for the discord.py client. This uses the old re-write without cogs, but it gets the job done!
"""


@bot.event  # Using self gives u
async def on_ready():  # I can make self optional by
    print("We have logged in as {0.user}".format(bot))


async def main():
    debug_guild = int(os.getenv("DEBUG_GUILD"))
    debug_channel = int(os.getenv("DEBUG_CHANNEL"))

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
        )
    )

    await bot.start(os.getenv("DISCORD_TOKEN"))


# Run the bot with a token taken from an environment file.
if __name__ == "__main__":

    PID_FILE = "bot.pid"
    if os.path.exists(PID_FILE):
        print("Process ID file already exists")
        sys.exit(1)
    else:
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))
            print("" "Wrote PID to f" "ile the file " + PID_FILE)
            f.close()
    try:
        asyncio.get_event_loop().run_until_complete(main())
    except KeyboardInterrupt:
        print("Caught keyboard interrupt, killing and removing PID")
        os.remove(PID_FILE)
    except Exception as e:
        print(str(e))
        print("Removing PID file")
        os.remove(PID_FILE)
    finally:
        sys.exit(0)
