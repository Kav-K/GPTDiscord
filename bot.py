import time

import discord
import openai
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
import os

class Mode:
    TOP_P = "top_p"
    TEMPERATURE = "temperature"

class Model:
    # An enum of two modes, TOP_P or TEMPERATURE
    def __init__(self, ):
        self._mode = Mode.TEMPERATURE
        self._temp = 0.7  # Higher value means more random, lower value means more likely to be a coherent sentence
        self._top_p = 1  # 1 is equivalent to greedy sampling, 0.1 means that the model will only consider the top 10% of the probability distribution
        self._max_tokens = 2000  # The maximum number of tokens the model can generate
        self._presence_penalty = 0  # Penalize new tokens based on whether they appear in the text so far
        self._frequency_penalty = 0  # Penalize new tokens based on their existing frequency in the text so far. (Higher frequency = lower probability of being chosen.)
        self._best_of = 1  # Number of responses to compare the loglikelihoods of
        self._prompt_min_length = 25

        openai.api_key = os.getenv('OPENAI_TOKEN')

    # Use the @property and @setter decorators for all the self fields to provide value checking
    @property
    def mode(self):
        return self._mode

    @mode.setter
    def mode(self, value):
        if value not in [Mode.TOP_P, Mode.TEMPERATURE]:
            raise ValueError("mode must be either 'top_p' or 'temperature'")
        if value == Mode.TOP_P:
            self._top_p = 0.5
            self._temp = 1
        elif value == Mode.TEMPERATURE:
            self._top_p = 1
            self._temp = 0.7

        self._mode = value

    @property
    def temp(self):
        return self._temp

    @temp.setter
    def temp(self, value):
        value = float(value)
        if value < 0 or value > 1:
            raise ValueError("temperature must be greater than 0 and less than 1, it is currently " + str(value))
        if self._mode == Mode.TOP_P:
            raise ValueError("Cannot set temperature when in top_p mode")

        self._temp = value

    @property
    def top_p(self):
        return self._top_p

    @top_p.setter
    def top_p(self, value):
        value = float(value)
        if value < 0 or value > 1:
            raise ValueError("top_p must be greater than 0 and less than 1, it is currently " + str(value))
        if self._mode == Mode.TEMPERATURE:
            raise ValueError("Cannot set top_p when in temperature mode")
        self._top_p = value

    @property
    def max_tokens(self):
        return self._max_tokens

    @max_tokens.setter
    def max_tokens(self, value):
        value = int(value)
        if value < 15 or value > 4096:
            raise ValueError("max_tokens must be greater than 15 and less than 4096, it is currently " + str(value))
        self._max_tokens = value

    @property
    def presence_penalty(self):
        return self._presence_penalty

    @presence_penalty.setter
    def presence_penalty(self, value):
        if int(value) < 0:
            raise ValueError("presence_penalty must be greater than 0, it is currently " + str(value))
        self._presence_penalty = value

    @property
    def frequency_penalty(self):
        return self._frequency_penalty

    @frequency_penalty.setter
    def frequency_penalty(self, value):
        if int(value) < 0:
            raise ValueError("frequency_penalty must be greater than 0, it is currently " + str(value))
        self._frequency_penalty = value

    @property
    def best_of(self):
        return self._best_of

    @best_of.setter
    def best_of(self, value):
        value = int(value)
        if value < 1 or value > 3:
            raise ValueError("best_of must be greater than 0 and ideally less than 3 to save tokens, it is currently " + str(value))
        self._best_of = value

    @property
    def prompt_min_length(self):
        return self._prompt_min_length

    @prompt_min_length.setter
    def prompt_min_length(self, value):
        value = int(value)
        if value < 10 or value > 4096:
            raise ValueError("prompt_min_length must be greater than 10 and less than 4096, it is currently " + str(value))
        self._prompt_min_length = value

    def send_request(self, prompt):
        # Validate that  all the parameters are in a good state before we send the request
        if len(prompt) < self.prompt_min_length:
            raise ValueError("Prompt must be greater than 25 characters, it is currently " + str(len(prompt)))

        print("The prompt about to be sent is " + prompt)

        response = openai.Completion.create(
            model="text-davinci-003",
            prompt=prompt,
            temperature=self.temp,
            top_p=self.top_p,
            max_tokens=self.max_tokens,
            presence_penalty=self.presence_penalty,
            frequency_penalty=self.frequency_penalty,
            best_of=self.best_of,
        )
        print(response.__dict__)
        return response


bot = commands.Bot(command_prefix="gpt3 ")
model = Model()
last_used = {}
GLOBAL_COOLDOWN_TIME = 5  # In seconds

class DiscordBot:

    def __init__(self, bot):
        self.bot = bot
        bot.run(os.getenv('DISCORD_TOKEN'))
        self.last_used = {}

    @staticmethod
    @bot.event # Using self gives u
    async def on_ready(): # I can make self optional by
        print('We have logged in as {0.user}'.format(bot))

    @staticmethod
    @bot.event
    async def on_message(message):
        if message.author == bot.user:
            return

        if not message.content.startswith('!g'):
            return

        # Implement a global 20 second timer for using the bot:
        # If the user has used the bot in the last 20 seconds, don't let them use it again
        # We can implement that lie this:
        if message.author.id in last_used:
            if time.time() - last_used[message.author.id] < GLOBAL_COOLDOWN_TIME:
                # Tell the user the remaining global cooldown time, respond to the user's original message as a "reply"
                await message.reply("You must wait " + str(round(GLOBAL_COOLDOWN_TIME - (time.time() - last_used[message.author.id]))) + " seconds before using the bot again")
                return

        last_used[message.author.id] = time.time()

        # Print settings command
        if message.content == "!g":
            # create a discord embed with help text
            embed = discord.Embed(title="GPT3Bot Help", description="The current commands", color=0x00ff00)
            embed.add_field(name="!g <prompt>", value="Ask GPT3 something. Be clear, long, and concise in your prompt. Don't waste tokens.", inline=False)
            embed.add_field(name="!gp", value="Print the current settings of the model", inline=False)
            embed.add_field(name="!gs <model parameter> <value>", value="Change the parameter of the model named by <model parameter> to new value <value>", inline=False)
            embed.add_field(name="!g", value="See this help text", inline=False)
            await message.channel.send(embed=embed)


        elif message.content.startswith('!gp'):
            embed = discord.Embed(title="GPT3Bot Settings", description="The current settings of the model", color=0x00ff00)
            for key, value in model.__dict__.items():
                embed.add_field(name=key, value=value, inline=False)
            await message.channel.send(embed=embed)

        elif message.content.startswith('!gs'):
            # Extract the parameter and the value
            parameter = message.content[4:].split()[0]
            value = message.content[4:].split()[1]
            # Check if the parameter is a valid parameter
            if hasattr(model, parameter):
                # Check if the value is a valid value
                try:
                    # Set the parameter to the value
                    setattr(model, parameter, value)
                    await message.channel.send("Successfully set the parameter " + parameter + " to " + value)

                    if parameter == "mode":
                        await message.channel.send(
                            "The mode has been set to " + value + ". This has changed the temperature top_p to the mode defaults of " + str(
                                model.temp) + " and " + str(model.top_p))
                except ValueError as e:
                    await message.channel.send(e)
            else:
                await message.channel.send("The parameter is not a valid parameter")

        # GPT3 command
        elif message.content.startswith('!g'):
            # Extract all the text after the !g and use it as the prompt.
            prompt = message.content[2:]
            # Send the request to the model
            try:
                response = model.send_request(prompt)
                response_text = response["choices"][0]["text"]
                print(response_text)

                # If the response text is > 3500 characters, paginate and send
                if len(response_text) > 1900:
                    # Split the response text into 3500 character chunks
                    response_text = [response_text[i:i + 1900] for i in range(0, len(response_text), 1900)]
                    # Send each chunk as a message
                    for chunk in response_text:
                        await message.channel.send(chunk)
                else:
                    await message.channel.send(response_text)

            except ValueError as e:
                await message.channel.send(e)
                return
            except Exception as e:
                await message.channel.send("Something went wrong, please try again later")
                await message.channel.send(e)
                return

# Run the bot with a token taken from an environment file.
if __name__ == "__main__":
    bot = DiscordBot(bot)

