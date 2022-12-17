# Requirements
`python3.9 -m pip install -r requirements.txt`

**I reccomend using python 3.9!**

OpenAI API Key (https://beta.openai.com/docs/api-reference/introduction)

Discord Bot Token (https://discord.com/developers/applications)

You can learn how to add the discord bot to your server via https://www.ionos.co.uk/digitalguide/server/know-how/creating-discord-bot/

Both the OpenAI API key and the Discord bot token needed to be loaded into a .env file in the same local directory as the bot file.

You also need to add a DEBUG_GUILD id and a DEBUG_CHANNEL id, the debug guild id is a server id, and the debug channel id is a text-channel id in Discord. Your final .env file should look like the following:

```
OPENAI_TOKEN="<openai_api_token>"

DISCORD_TOKEN="<discord_bot_token>"

DEBUG_GUILD="974519864045756446"   #discord_server_id

DEBUG_CHANNEL="977697652147892304"  #discord_chanel_id
```

Optionally, you can include your own conversation starter text for the bot that's used with `!g converse`, with `CONVERSATION_STARTER_TEXT`

## Bot on discord:

- Create a new Bot on Discord Developer Portal:
    - Applications -> New Application
- Generate Toker for the app (discord_bot_token)
    - Select App (Bot) -> Bot -> Reset Token
- Toogle PRESENCE INTENT:
    - Select App (Bot) -> Bot -> PRESENCE INTENT
- Add Bot the the server.
    - Select App (Bot) -> OAuth2 -> URL Generator -> Select Scope: Bot
    - Bot Permissions will appear, select the desired permissions
    - Copy the link generated below and paste it on the browser
    - On add to server select the desired server to add the bot

# Usage

`python3.9 main.py`

# Commands

`!g` - Display help text for the bot

`!g converse` - Start a conversation with the bot, like ChatGPT

`!g end` - End a conversation with the bot.

`!gp` - Display settings for the model (temperature, top_p, etc)

`!gs <setting> <value>` - Change a model setting to a new value

`!g <prompt>` Ask the GPT3 Davinci 003 model a question.

`!gu` Estimate current usage details (based on davinci)

`!gs low_usage_mode True/False` Turn low usage mode on and off. If on, it will use the curie-001 model, and if off, it will use the davinci-003 model.
