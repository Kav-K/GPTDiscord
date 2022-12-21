# Screenshots

<p align="center">
<img src="https://i.imgur.com/KeLpDgj.png"/>
<img  src="https://i.imgur.com/AFCwxgJ.png"/>

</p>

# Recent Major Updates

- **AUTOMATIC CHAT SUMMARIZATION!** - When the context limit of a conversation is reached, the bot will use GPT3 itself to summarize the conversation to reduce the tokens, and continue conversing with you, this allows you to chat for a long time!

- **DALL-E Image Generation**

- **REDO ON EDIT** - When you edit a prompt, it will automatically be resent to GPT3 and the response updated!

# Features
- **Directly prompt GPT3 with `!g <prompt>`**

- **Have conversations with the bot, just like chatgpt, with `!g converse`** - Conversations happen in threads that get automatically cleaned up!

- **DALL-E Image Generation** - Generate DALL-E AI images right in discord with `!draw <prompt>`! It even supports multiple image qualities, multiple images, creating image variants, retrying, and saving images.

- **Redo Requests** - A simple button after the GPT3 response or DALL-E generation allows you to redo the initial prompt you asked.

- **DALL-E Image Prompt Optimization** - Given some text that you're trying to generate an image for, the bot will automatically optimize the text to be more DALL-E friendly!

- Automatically re-send your prompt and update the response in place if you edit your original prompt!

- Change and view model parameters such as temp, top_p, and etc directly within discord. 
- Tracks token usage automatically
- Automatic pagination and discord support, the bot will automatically send very long message as multiple messages, and is able to send discord code blocks and emoji, gifs, etc.
- A low usage mode, use a command to automatically switch to a cheaper and faster model to conserve your tokens during times of peak usage. 
- Prints debug to a channel of your choice, so you can view the raw response JSON
- Ability to specify a limit to how long a conversation can be with the bot, to conserve your tokens.


# Requirements
`python3.9 -m pip install -r requirements.txt`

**I recommend using python 3.9!**

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

## Server Installation

First, you want to get a server, for this guide, I will be using DigitalOcean as the host. 

For instructions on how to get a server from start to finish, they are available on DigitalOcean's website directly from the community, available here: https://www.digitalocean.com/community/tutorials/how-to-set-up-an-ubuntu-20-04-server-on-a-digitalocean-droplet. Ignore the part about setting up an "ssh key", and just use a password instead. 

After you set up the server, the DigitalOcean GUI will give you an IP address, copy this IP address. Afterwards, you will need to SSH into the server. This can be done using a program such as "PuTTy", or by using your commandline, if it's supported. To login to the server, your username will be "root", your password will be the password that you defined earlier when setting up the droplet, and the IP address will be the IP address you copied after the droplet was finished creation.

To connect with ssh, run the following command in terminal:
`ssh root@{IP ADDRESS}`

It will then prompt you for your password, which you should enter, and then you will be logged in. 

After login, we need to install the various dependencies that the bot needs. To do this, we will run the following commands:

```
# Download the source code.
    1  ls
    2  git clone https://github.com/Kav-K/GPT3Discord.git
    3  ls
    4  cd GPT3Discord/
    5  ls
# Install system packages (python)
    6  sudo apt-get update
    7  sudo apt install software-properties-common
    8  sudo add-apt-repository ppa:deadsnakes/ppa
    9  sudo apt install python3.9
# This command below should return "Python 3.9.x", if it is working. Otherwise, don't proceed.
   10  python3.9 --version 
   11  ls
   13  curl https://bootstrap.pypa.io/get-pip.py -o get-pip.py
   14  python3.9 get-pip.py
# Install project dependencies
   15  python3.9 -m pip install -r requirements.txt
   16  ls
# Copy the sample.env file into a regular .env file. You will need to edit this file.
   17  scp sample.env .env
# The command below is used to edit the .env file and to put in your API keys. You can right click within the
# editor after running this command to paste. When you are done editing, press CTRL + X, and then type Y, to save.
   18  nano .env
   19  ls
# Run the bot.
   20  python3.9 main.py
   21  ls

```


## Bot on discord:

- Create a new Bot on Discord Developer Portal:
    - Applications -> New Application
- Generate Toker for the app (discord_bot_token)
    - Select App (Bot) -> Bot -> Reset Token
- Toogle PRESENCE INTENT:
    - Select App (Bot) -> Bot -> PRESENCE INTENT, SERVER MEMBERS INTENT, MESSAGES INTENT, (basically turn on all intents)
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

`!draw <prompt>` - Have DALL-E generate images based on a prompt

`!gp` - Display settings for the model (temperature, top_p, etc)

`!gs <setting> <value>` - Change a model setting to a new value

`!g <prompt>` Ask the GPT3 Davinci 003 model a question.

`!gu` Estimate current usage details (based on davinci)

`!gs low_usage_mode True/False` Turn low usage mode on and off. If on, it will use the curie-001 model, and if off, it will use the davinci-003 model.

`!imgoptimize <image prompt text>` Optimize a given prompt text for DALL-E image generation.

`!delete_all_conversation_threads` - Delete all threads related to this bot across all servers.

# Configuration

All the model parameters are configurable inside discord. Type `!gp` to view all the configurable parameters, and use `!gs <param> <value>` to set parameters. For example, if I wanted to change the number of images generated by DALL-E by default to 4, I can type the following command in discord: `!gs num_images 4`
