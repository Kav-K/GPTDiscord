# Requirements
`python3.7 -m pip install openai`

`python3.7 -m pip install dotenv-python`

`python3.7 -m pip install discord` (This should be the discord.py rewrite, not the pycord library)

OpenAI API Key (https://beta.openai.com/docs/api-reference/introduction)

Discord Bot Token (https://discord.com/developers/applications)

You can learn how to add the discord bot to your server via https://www.ionos.co.uk/digitalguide/server/know-how/creating-discord-bot/

Both the OpenAI API key and the Discord bot token needed to be loaded into a .env file in the same local directory as the bot file.

```
OPENAI_TOKEN="TOKEN"

DISCORD_TOKEN="TOKEN"
```

# Usage

`python3.7 bot.py`

# Commands

`!g` - Display help text for the bot

`!g converse` - Start a conversation with the bot, like ChatGPT

`!g end` - End a conversation with the bot.

`!gp` - Display settings for the model (temperature, top_p, etc)

`!gs <setting> <value>` - Change a model setting to a new value

`!g <prompt>` Ask the GPT3 Davinci 003 model a question.

`!gu` Estimate current usage details (based on davinci)

`!gs low_usage_mode True/False` Turn low usage mode on and off. If on, it will use the curie-001 model, and if off, it will use the davinci-003 model.
