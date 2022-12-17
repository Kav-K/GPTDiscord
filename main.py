import asyncio

import discord
from discord.ext import commands
from dotenv import load_dotenv

from cogs.gpt_3_commands_and_converser import GPT3ComCon
from cogs.image_prompt_optimizer import ImgPromptOptimizer
from models.message_model import Message
from models.openai_model import Model
from models.usage_service_model import UsageService

load_dotenv()
import os

"""
Message queueing for the debug service, defer debug messages to be sent later so we don't hit rate limits.
"""
message_queue = asyncio.Queue()
asyncio.ensure_future(Message.process_message_queue(message_queue, 1.5, 5))


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
    print('We have logged in as {0.user}'.format(bot))

async def main():
    debug_guild = int(os.getenv('DEBUG_GUILD'))
    debug_channel = int(os.getenv('DEBUG_CHANNEL'))

    # Load the main GPT3 Bot service
    bot.add_cog(GPT3ComCon(bot, usage_service, model, message_queue, debug_guild, debug_channel))
    bot.add_cog(ImgPromptOptimizer(bot, usage_service, model, message_queue))

    await bot.start(os.getenv('DISCORD_TOKEN'))


# Run the bot with a token taken from an environment file.
if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())




