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
        self._max_conversation_length = 5

        openai.api_key = os.getenv('OPENAI_TOKEN')
    # Use the @property and @setter decorators for all the self fields to provide value checking

    @property
    def max_conversation_length(self):
        return self._max_conversation_length

    @max_conversation_length.setter
    def max_conversation_length(self, value):
        if value < 1:
            raise ValueError("Max conversation length must be greater than 1")
        if value > 20:
            raise ValueError("Max conversation length must be less than 20, this will start using credits quick.")
        self._max_conversation_length = value

    @property
    def mode(self):
        return self._mode

    @mode.setter
    def mode(self, value):
        if value not in [Mode.TOP_P, Mode.TEMPERATURE]:
            raise ValueError("mode must be either 'top_p' or 'temperature'")
        if value == Mode.TOP_P:
            self._top_p = 0.1
            self._temp = 0.7
        elif value == Mode.TEMPERATURE:
            self._top_p = 0.9
            self._temp = 0.6

        self._mode = value

    @property
    def temp(self):
        return self._temp

    @temp.setter
    def temp(self, value):
        value = float(value)
        if value < 0 or value > 1:
            raise ValueError("temperature must be greater than 0 and less than 1, it is currently " + str(value))

        self._temp = value

    @property
    def top_p(self):
        return self._top_p

    @top_p.setter
    def top_p(self, value):
        value = float(value)
        if value < 0 or value > 1:
            raise ValueError("top_p must be greater than 0 and less than 1, it is currently " + str(value))
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
            raise ValueError(
                "best_of must be greater than 0 and ideally less than 3 to save tokens, it is currently " + str(value))
        self._best_of = value

    @property
    def prompt_min_length(self):
        return self._prompt_min_length

    @prompt_min_length.setter
    def prompt_min_length(self, value):
        value = int(value)
        if value < 10 or value > 4096:
            raise ValueError(
                "prompt_min_length must be greater than 10 and less than 4096, it is currently " + str(value))
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
conversating_users = {}


class User:

    def __init__(self, id):
        self.id = id
        self.history = ""
        self.count = 0

    # These user objects should be accessible by ID, for example if we had a bunch of user
    # objects in a list, and we did `if 1203910293001 in user_list`, it would return True
    # if the user with that ID was in the list
    def __eq__(self, other):
        return self.id == other.id

    def __hash__(self):
        return hash(self.id)

    def __repr__(self):
        return f"User(id={self.id}, history={self.history})"

    def __str__(self):
        return self.__repr__()


class DiscordBot:

    def __init__(self, bot):
        self.bot = bot
        bot.run(os.getenv('DISCORD_TOKEN'))
        self.last_used = {}

    @staticmethod
    @bot.event  # Using self gives u
    async def on_ready():  # I can make self optional by
        print('We have logged in as {0.user}'.format(bot))

    @staticmethod
    @bot.event
    async def on_message(message):
        if message.author == bot.user:
            return

        # Only allow the bot to be used by people who have the role "Admin" or "GPT"
        if not any(role.name in ["admin", "Admin", "GPT", "gpt"] for role in message.author.roles):
            return

        if not message.content.startswith('!g'):
            return

        # Implement a global 20 second timer for using the bot:
        # If the user has used the bot in the last 20 seconds, don't let them use it again
        # We can implement that lie this:
        if message.author.id in last_used:
            if time.time() - last_used[message.author.id] < GLOBAL_COOLDOWN_TIME:
                # Tell the user the remaining global cooldown time, respond to the user's original message as a "reply"
                await message.reply("You must wait " + str(round(GLOBAL_COOLDOWN_TIME - (
                            time.time() - last_used[message.author.id]))) + " seconds before using the bot again")
                return

        last_used[message.author.id] = time.time()

        # Print settings command
        if message.content == "!g":
            # create a discord embed with help text
            embed = discord.Embed(title="GPT3Bot Help", description="The current commands", color=0x00ff00)
            embed.add_field(name="!g <prompt>",
                            value="Ask GPT3 something. Be clear, long, and concise in your prompt. Don't waste tokens.",
                            inline=False)
            embed.add_field(name="!g converse",
                            value="Start a conversation with GPT3",
                            inline=False)
            embed.add_field(name="!g end",
                            value="End a conversation with GPT3",
                            inline=False)
            embed.add_field(name="!gp", value="Print the current settings of the model", inline=False)
            embed.add_field(name="!gs <model parameter> <value>",
                            value="Change the parameter of the model named by <model parameter> to new value <value>",
                            inline=False)
            embed.add_field(name="!g", value="See this help text", inline=False)
            await message.channel.send(embed=embed)


        elif message.content.startswith('!gp'):
            embed = discord.Embed(title="GPT3Bot Settings", description="The current settings of the model",
                                  color=0x00ff00)
            for key, value in model.__dict__.items():
                embed.add_field(name=key, value=value, inline=False)
            await message.reply(embed=embed)

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
                    await message.reply("Successfully set the parameter " + parameter + " to " + value)

                    if parameter == "mode":
                        await message.reply(
                            "The mode has been set to " + value + ". This has changed the temperature top_p to the mode defaults of " + str(
                                model.temp) + " and " + str(model.top_p))
                except ValueError as e:
                    await message.reply(e)
            else:
                await message.reply("The parameter is not a valid parameter")

        # GPT3 command
        elif message.content.startswith('!g'):
            # Extract all the text after the !g and use it as the prompt.
            prompt = message.content[2:]
            # Remove the extra space on the left
            prompt = prompt.lstrip()

            # If the prompt is just "converse", start a conversation with GPT3
            if prompt == "converse":
                # If the user is already conversating, don't let them start another conversation
                if message.author.id in conversating_users:
                    await message.reply("You are already conversating with GPT3. End the conversation with !g end")
                    return

                # If the user is not already conversating, start a conversation with GPT3
                conversating_users[message.author.id] = User(message.author.id)
                # Append the starter text for gpt3 to the user's history so it gets concatenated with the prompt later
                conversating_users[message.author.id].history += "You are an artificial intelligence that is able to do anything, and answer any question, I want you to be my personal assisstant and help me with some tasks."
                await message.reply("You are now conversing with GPT3. End the conversation with !g end")
                return

            # If the prompt is just "end", end the conversation with GPT3
            if prompt == "end":
                # If the user is not conversating, don't let them end the conversation
                if message.author.id not in conversating_users:
                    await message.reply("You are not conversing with GPT3. Start a conversation with !g converse")
                    return

                # If the user is conversating, end the conversation
                conversating_users.pop(message.author.id)
                await message.reply("You have ended the conversation with GPT3. Start a conversation with !g converse")
                return

            # We want to have conversationality functionality. To have gpt3 remember context, we need to append the conversation/prompt
            # history to the prompt. We can do this by checking if the user is in the conversating_users dictionary, and if they are,
            # we can append their history to the prompt.
            if message.author.id in conversating_users:
                prompt = conversating_users[message.author.id].history + "\nHuman: " + prompt + "\nAI:"
                # Now, add overwrite the user's history with the new prompt
                conversating_users[message.author.id].history = prompt

                # increment the conversation counter for the user
                conversating_users[message.author.id].count += 1

                # If the user has reached the max conversation length, end the conversation
                if conversating_users[message.author.id].count >= model.max_conversation_length:
                    conversating_users.pop(message.author.id)
                    await message.reply("You have reached the maximum conversation length. You have ended the conversation with GPT3, and it has ended.")
                    return


            # Send the request to the model
            try:
                response = model.send_request(prompt)
                response_text = response["choices"][0]["text"]
                print(response_text)

                # If the user is conversating, we want to add the response to their history
                if message.author.id in conversating_users:
                    conversating_users[message.author.id].history += response_text + "\n"

                # If the response text is > 3500 characters, paginate and send
                if len(response_text) > 1900:
                    # Split the response text into 3500 character chunks
                    response_text = [response_text[i:i + 1900] for i in range(0, len(response_text), 1900)]
                    # Send each chunk as a message
                    first = False
                    for chunk in response_text:
                        if not first:
                            await message.reply(chunk)
                            first = True
                        else:
                            await message.channel.send(chunk)
                else:
                    await message.reply(response_text)

            except ValueError as e:
                await message.reply(e)
                return
            except Exception as e:
                await message.reply("Something went wrong, please try again later")
                await message.channel.send(e)
                return


# Run the bot with a token taken from an environment file.
if __name__ == "__main__":
    bot = DiscordBot(bot)
