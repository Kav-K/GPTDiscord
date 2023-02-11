![Docker](https://github.com/Kav-K/GPT3Discord/actions/workflows/docker_upload.yml/badge.svg)
![PyPi](https://github.com/Kav-K/GPT3Discord/actions/workflows/pypi_upload.yml/badge.svg)
![Build](https://github.com/Kav-K/GPT3Discord/actions/workflows/build.yml/badge.svg)

[![PyPi version](https://badgen.net/pypi/v/gpt3discord/)](https://pypi.org/project/gpt3discord)
[![Latest release](https://badgen.net/github/release/Kav-K/GPT3Discord)](https://github.com/Kav-K/GPT3Discord/releases)
[![Maintenance](https://img.shields.io/badge/Maintained%3F-yes-green.svg)](https://GitHub.com/Kav-K/GPT3Discord/graphs/commit-activity)
[![GitHub license](https://img.shields.io/github/license/Kav-K/GPT3Discord)](https://github.com/Kav-K/GPT3Discord/blob/master/LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=flat-square)](http://makeapullrequest.com)

# Overview
<p align="center">
<img src="https://i.imgur.com/KeLpDgj.png"/>
</p>
A robust, all-in-one GPT3 interface for Discord. Chat just like ChatGPT right inside Discord! Generate beautiful AI art using DALL-E 2! Automatically moderate your server using AI! Upload documents, videos, and files to get AI-assisted insights! A thorough integration with permanent conversation memory, automatic request retry, fault tolerance and reliability for servers of any scale, and much more.

SUPPORT SERVER FOR BOT SETUP: https://discord.gg/WvAHXDMS7Q (You can try out the bot here also in a limited fashion)

# Features
- **Directly prompt GPT3 with `/gpt ask <prompt>`**

- **Have long term, permanent conversations with the bot, just like chatgpt, with `/gpt converse`** - Conversations happen in threads that get automatically cleaned up!

- **Custom Indexes** - Use your own files, pdfs, txt files, websites, discord channel content as context when asking GPT3 questions!

- **AI-Assisted Google Search** - Speaks for itself!

- **DALL-E Image Generation** - Generate DALL-E AI images right in discord with `/dalle draw <prompt>`! It even supports multiple image qualities, multiple images, creating image variants, retrying, and saving images.

- **DALL-E Image Prompt Optimization** - Given some text that you're trying to generate an image for, the bot will automatically optimize the text to be more DALL-E friendly! `/dalle optimize <prompt>`

- **Edit Requests** - Ask GPT to edit a piece of text or code with a given instruction. `/gpt edit <instruction> <text>`

- **DeepL Translations** - Translate text with DeepL. `/translate <text>`

- **Redo Requests** - A simple button after the GPT3 response or DALL-E generation allows you to redo the initial prompt you asked. You can also redo conversation messages by just editing your message!

- **Automatic AI-Based Server Moderation** - Moderate your server automatically with AI!

- **Auto-retry on API errors** - Automatically resend failed requests to OpenAI's APIs!

- Automatically re-send your prompt and update the response in place if you edit your original prompt!

- Async and fault tolerant, **can handle hundreds of users at once**, if the upstream API permits!
- Change and view model parameters such as temp, top_p, and etc directly within discord. 
- Tracks token usage automatically
- Automatic pagination and discord support, the bot will automatically send very long message as multiple messages, and is able to send discord code blocks and emoji, gifs, etc.
- A low usage mode, use a command to automatically switch to a cheaper and faster model to conserve your tokens during times of peak usage. 
- Prints debug to a channel of your choice, so you can view the raw response JSON
- Ability to specify a limit to how long a conversation can be with the bot, to conserve your tokens.
If you want to see other features, click below
[**Other Features**](https://github.com/Kav-K/GPT3Discord/tree/main/recent-updates-and-implementations)
If you want to see recent updates and to read more about implementations like 
-AI Assisted Google Search
-Custom Index / Knowledge base
-Permanent Memory and Conversations
-Translation with Deep L
-User Input Api Keys
click below
[**Updates and Implementations**](https://github.com/Kav-K/GPT3Discord/tree/main/recent-updates-and-implementations)


# How to set up GPT3Discord

## Before installing

[**GPT3Discord Guides**](https://github.com/Kav-K/GPT3Discord/tree/main/detailed_guides)

Before you start, be sure to click the link above. This will give you detailed step-by-step guides on setting up:

Droplet with Digital Ocean
OpenAI API Key
Azure App Service

These will help make the process easy and done quickly.If you still run into problems or have suggestions for improving the guides, you can join the [**Discord-Server**](https://discord.gg/WvAHXDMS7Q) and we will try to help you. Keep in mind that the maintainers are volunteers and will try to help you on their schedule.


*The number and content of the guides is constantly adapted to current requirements.*


### Configuration

All the model parameters are configurable inside discord. Type `/system settings` to view all the configurable parameters, and use `/system settings <param> <value>` to set parameters. 

For example, if I wanted to change the number of images generated by DALL-E by default to 4, I can type the following command in discord: `/system settings num_images 4`


### Requirements and Usage
**For OCR, and document functionalities**:
```
pip3 install torch==1.9.1+cpu torchvision==0.10.1+cpu -f https://download.pytorch.org/whl/torch_stable.html
or
python3.9 -m pip install torch==1.9.1+cpu torchvision==0.10.1+cpu -f https://download.pytorch.org/whl/torch_stable.html
```
**For audio extraction for indexing from .mp3 and .mp4 files**:

`python3.9 -m pip install git+https://github.com/openai/whisper.git`

**All other dependencies**:

`python3.9 -m pip install -r requirements.txt`

**We recommend using python 3.9.**

OpenAI API Key (https://beta.openai.com/docs/api-reference/introduction)

Discord Bot Token (https://discord.com/developers/applications)

The bot uses an environment file named `.env` to configure it. This file must be named exactly `.env` and placed in the same directory as `gpt3discord.py`. Within this file, you need to fill in your `OPENAI_TOKEN`, `DISCORD_TOKEN`, `DEBUG_SERVER`, and `DEBUG_CHANNEL`, and `ALLOWED_GUILDS` to get the bot to work. There are also many other configurable options, an example `.env` file is shown below.
```shell
OPENAI_TOKEN = "<openai_api_token>"
DISCORD_TOKEN = "<discord_bot_token>"
#PINECONE_TOKEN = "<pinecone_token>" # pinecone token if you have it enabled. See readme
DEBUG_GUILD = "974519864045756446"  # discord_server_id
DEBUG_CHANNEL = "977697652147892304"  # discord_chanel_id
ALLOWED_GUILDS = "971268468148166697,971268468148166697"
# People with the roles in ADMIN_ROLES can use admin commands like /clear-local, and etc
ADMIN_ROLES = "Admin,Owner"
# People with the roles in DALLE_ROLES can use commands like /dalle draw or /dalle imgoptimize
DALLE_ROLES = "Admin,Openai,Dalle,gpt"
# People with the roles in GPT_ROLES can use commands like /gpt ask or /gpt converse
GPT_ROLES = "openai,gpt"
WELCOME_MESSAGE = "Hi There! Welcome to our Discord server. We hope you'll enjoy our server and we look forward to engaging with you!"  # This is a fallback message if gpt3 fails to generate a welcome message.
USER_INPUT_API_KEYS="False" # If True, users must use their own API keys for OpenAI. If False, the bot will use the API key in the .env file.
# Moderations Service alert channel, this is where moderation alerts will be sent as a default if enabled
MODERATIONS_ALERT_CHANNEL = "977697652147892304"
# User API key db path configuration. This is where the user API keys will be stored.
USER_KEY_DB_PATH = "user_key_db.sqlite"
```
### Permissions

As mentioned in the comments of the sample environment file, there are three permission groups that you can edit in the environment (`.env`) file. `ADMIN_ROLES` are roles that allow users to use `/system` and `/mod` commands. `GPT_ROLES` are roles that allow users to use `/gpt` commands, and `DALLE_ROLES` are roles that allow users to use `/dalle` commands. `TRANSLATE_ROLES` allows users to use translation, `INDEX_ROLES` allow users to use custom indexing commands, and `SEARCH_ROLES` allows users to use the search functionality.


If for a command group you want everybody to be able to use those commands, just don't include the relevant line in the `.env` file. For example, if you want everyone to be able to use GPT3 commands, you can just omit `the GPT_ROLES="...."` line.

## Installation

### Create the bot

https://discordpy.readthedocs.io/en/stable/discord.html

- Create a new Bot on Discord Developer Portal:
    - Applications -> New Application
- Generate Token for the app (discord_bot_token)
    - Select App (Bot) -> Bot -> Reset Token
- Toogle PRESENCE INTENT:
    - Select App (Bot) -> Bot -> PRESENCE INTENT, SERVER MEMBERS INTENT, MESSAGES INTENT, (basically turn on all intents)
- Add Bot the the server.
    - Select App (Bot) -> OAuth2 -> URL Generator -> Select Scope: Bot, application.commands
    - Bot Permissions will appear, select the desired permissions
    - Copy the link generated below and paste it on the browser
    - On add to server select the desired server to add the bot
- Make sure you have updated your .env file with valid values for `DEBUG_GUILD`, `DEBUG_CHANNEL` and `ALLOWED_GUILDS`, otherwise the bot will not work. Guild IDs can be found by right clicking a server and clicking `Copy ID`, similarly, channel IDs can be found by right clicking a channel and clicking `Copy ID`.


### Server Installation

First, you want to get a server, for this guide, I will be using DigitalOcean as the host. 

For instructions on how to get a server from start to finish, they are available on DigitalOcean's website directly from the community, available here: https://www.digitalocean.com/community/tutorials/how-to-set-up-an-ubuntu-20-04-server-on-a-digitalocean-droplet. Ignore the part about setting up an "ssh key", and just use a password instead. 

**Please sign up for a DigitalOcean account using my referral link if you'd like to support me https://m.do.co/c/e31eff1231a4**

After you set up the server, the DigitalOcean GUI will give you an IP address, copy this IP address. Afterwards, you will need to SSH into the server. This can be done using a program such as "PuTTy", or by using your commandline, if it's supported. To login to the server, your username will be "root", your password will be the password that you defined earlier when setting up the droplet, and the IP address will be the IP address you copied after the droplet was finished creation.

To connect with ssh, run the following command in terminal:
`ssh root@{IP ADDRESS}`

It will then prompt you for your password, which you should enter, and then you will be logged in. 

After login, we need to install the various dependencies that the bot needs. To do this, we will run the following commands:
```bash
git clone https://github.com/Kav-K/GPT3Discord.git
cd GPT3Discord/

# Install system packages (python)
sudo apt-get update
sudo apt install software-properties-common
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt install python3.9
sudo apt install python3.9-distutils # If this doesn't work, try sudo apt install python3-distutils

# Install Pip for python3.9
curl https://bootstrap.pypa.io/get-pip.py -o get-pip.py
python3.9 get-pip.py

# Install project dependencies
python3.9 -m pip install --ignore-installed PyYAML
python3.9 -m pip install torch==1.9.1+cpu torchvision==0.10.1+cpu -f https://download.pytorch.org/whl/torch_stable.html
python3.9 -m pip install -r requirements.txt
python3.9 -m pip install .

# Copy the sample.env file into a regular .env file. `DEBUG_GUILD` and the ID for `ALLOWED_GUILDS` can be found by right-clicking your server and choosing "Copy ID". Similarly, `DEBUG_CHANNEL` can be found by right-clicking your debug channel.
cp sample.env .env

# The command below is used to edit the .env file and to put in your API keys. You can right click within the
# editor after running this command to paste. When you are done editing, press CTRL + X, and then type Y, to save.
nano .env

# Run the bot using [screen](https://www.gnu.org/software/screen/manual/screen.html) to keep it running after you disconnect from your SSH session:
screen gpt3discord

# Hit `Ctrl+a` then `d` to detach from the running bot.
# The bot's screen session can be reattached:
screen -r
```

If the last few commands don't allow the bot to run `screen gpt3discord`, you can attempt to run the bot another way:
```bash
{Navigate to the folder where the project files are}
screen -dmS GPTBot bash -c 'python3.9 gpt3discord.py'

# Reattach to screen session
screen -x # will reattach if this is the only screen session, if there are multiple, it will show IDs
# If there are multiple IDs returned by screen -x:
screen -d -r {ID} # replace {ID} with the ID of the screen session you want to reattach to

```

As a last resort, you can try to run the bot using python in a basic way, with simply
```bash
cd (the folder where the files for GPT3Discord are located/cloned)
python3.9 gpt3discord.py
```

### Docker Installation

We now have a `Dockerfile` in the repository. This will build / install all dependencies and put a `gpt3discord` binary (main.py) into path.
To build:

- [Install docker](https://docs.docker.com/get-docker/)
- Clone repository and build *(hopefully eventually we'll add CI to automatically build + push to docker hub)*
  - `docker build -t gpt3discord .`
  - *From repository root or supply path to repository*
- Make a .env file to bind mount to `/opt/gpt3discord/etc/environment`
- Optional: Make a data directory + bind mount it
  - Add `DATA_DIR=/data` to env file -> `usage.txt` is made here
  - Add `SHARE_DIR=/data/share` to env file -> this is where `conversation starters, optimizer pretext and the 'openers' folder` is loaded from
  - If `SHARE_DIR` is not included it'll load from the files added during the docker image build
- Run via docker:
  - `docker run [-d] --name gpt3discord -v env_file:/opt/gpt3discord/etc/environment [-v /containers/gpt3discord:/data] [-v /containers/gpt3discord/share:/data/share] gpt3discord`
  - You can also mount extra volumes and set `DATA_DIR` and `SHARE_DIR` in the env file to keep persistent data
  - `env_file` should be replaced with where you have your .env file stored on your machine

This can also be run via screen/tmux or detached like a daemon.


### Non-Server, Non-Docker installation (Windows included)

You need to install python3.9 and pip for python3.9 on your system.

With python3.9 installed and the requirements installed, you can run this bot anywhere. 

Install the dependencies with:
`pip3 install torch==1.9.1+cpu torchvision==0.10.1+cpu -f https://download.pytorch.org/whl/torch_stable.html`
`python3.9 -m pip install -r requirements.txt`

Then, run the bot with:
`python3.9 gpt3discord.py`

Here's a great video from a community member that shows an installation on Windows: https://youtu.be/xLhwS2rQg14

## Updating 

To update the bot, 

run (when working in the directory of GPT3discord):

```bash
#To get the latest branch:
git pull

#Install the latest modules so the bot keeps working.
python3.9 -m pip install -r requirements.txt
python3.9 -m pip install .
```
# Commands

These commands are grouped, so each group has a prefix but you can easily tab complete the command without the prefix. For example, for `/gpt ask`, if you type `/ask` and press tab, it'll show up too.

`/help` - Display help text for the bot

## (Chat)GPT3 Commands

`/gpt ask <prompt> <temp> <top_p> <frequency penalty> <presence penalty>` Ask the GPT3 Davinci 003 model a question. Optional overrides available

`/gpt edit <instruction> <input> <temp> <top_p> <codex>` Use the bot to edit text using the given instructions for how to do it, currently an alpha openai feature so results might vary. Codex uses a model trained on code. Editing is currently free

`/gpt converse <opener> <opener_file> <private> <minimal>` - Start a conversation with the bot, like ChatGPT

- `opener:<opener text>` - Start a conversation with the bot, with a custom opener text (this is useful if you want it to take on a custom personality from the start).

- `opener_file:<opener file name>.txt|.json` - Starts a conversation with the bot, using a custom file. 

  - Loads files from the `/openers` folder, has autocomplete support so files in the folder will show up. Added before the `opener` as both can be used at the same time

  - Custom openers need to be placed as a .txt file in the `openers` directory, in the same directory as `gpt3discord.py`

  - Enables minimal

  - Can use .json files in the `{"text": "your prompt", "temp":0, "top_p":0,"frequency_penalty":0,"presence_penalty":0}` format to include permanent overrides

- `private` - Start a private conversation with the bot, like ChatGPT

- `minimal` - Start a conversation with the bot, like ChatGPT, with minimal context (saves tokens)

`/gpt end` - End a conversation with the bot.

## DALL-E2 Commands

`/dalle draw <prompt>` - Have DALL-E generate images based on a prompt

`/dalle optimize <image prompt text>` Optimize a given prompt text for DALL-E image generation.

## Custom Indexes Commands

This bot supports per-user custom indexes. This means that users can upload files of their choosing, such as PDFs and ask GPT to answer questions based on those files.

`/index add file:<file> or link:<link>` - Use a document or use a link to create/add to your indexes. If you provide a youtube link, the transcript of the video will be used. If you provide a web url, the contents of the webpage will be used, if you provide an image, the image text will be extracted and used!

`/index query query:<prompt> nodes:<number> response_mode:<mode>` - Query your current index for a given prompt. GPT will answer based on your current document/index. You can also set it to query over more nodes, further refining the output over each one. A description of the modes can be found <a href="https://gpt-index.readthedocs.io/en/latest/guides/usage_pattern.html#setting-response-mode">here</a>. They do not work for deep composed indexes

`/index load user_index:<index> or server_index:<index>` - Load a previously created index you own yourself, or an index for the whole server.

`/index compose` - Combine multiple saved indexes into one, or upgrade existing indexes into Deep Compositions.

`/index reset` - Reset and delete all of your saved indexes

`/index add_discord channel:<discord channel>` - Create an add an index based on a discord channel

`/index discord_backup` - Use the last 3000 messages of every channel on your discord server as an index. Needs both an admin and a index role

## AI-Assisted Search

`/search query:<prompt> scope:<number of sites to visit> nodes:<how deep gpt3 should think>` - Search the web with GPT3. This command will search the web for you, and then ask GPT3 to answer your question based on the results. You can also set it to query over more nodes, further refining the output over each one.

## System and Settings

`/system settings` - Display settings for the model (temperature, top_p, etc)

`/system settings <setting> <value>` - Change a model setting to a new value. Has autocomplete support, certain settings will have autocompleted values too.

`/system usage` Estimate current usage details (based on davinci)

`/system settings low_usage_mode True/False` Turn low usage mode on and off. If on, it will use the curie-001 model, and if off, it will use the davinci-003 model.

`/system delete-conversation-threads` - Delete all threads related to this bot across all servers.

`/system local-size` - Get the size of the local dalleimages folder

`/system clear-local` - Clear all the local dalleimages.

## Automatic AI Moderation

`/mod set status:on` - Turn on automatic chat moderations. 

`/mod set status:off` - Turn off automatic chat moderations

`/mod set status:off alert_channel_id:<CHANNEL ID>` - Turn on moderations and set the alert channel to the channel ID you specify in the command.

`/mod config type:<warn/delete> hate:# hate_threatening:# self_harm:# sexual:# sexual_minors:# violence:# violence_graphic:#`
- Set the moderation thresholds of the bot for the specific type of moderation (`warn` or `delete`). You can view the thresholds by typing just `/mod config type:<warn/delete>` without any other parameters. You don't have to set all of them, you can just set one or two items if you want. For example, to set the hate threshold for warns, you can type `/mod config type:warn hate:0.2`
- Lower values are more strict, higher values are more lenient. There are default values that I've fine tuned the service with for a general server.

The bot needs Administrative permissions for this, and you need to set `MODERATIONS_ALERT_CHANNEL` to the channel ID of a desired channel in your .env file if you want to receive alerts about moderated messages.

This uses the OpenAI Moderations endpoint to check for messages, requests are only sent to the moderations endpoint at a MINIMUM request gap of 0.5 seconds, to ensure you don't get blocked and to ensure reliability. 

The bot uses numerical thresholds to determine whether a message is toxic or not, and I have manually tested and fine tuned these thresholds to a point that I think is good, please open an issue if you have any suggestions for the thresholds!

There are two thresholds for the bot, there are instances in which the bot will outright delete a message and an instance where the bot will send a message to the alert channel notifying admins and giving them quick options to delete and timeout the user (check out the screenshots at the beginning of the README to see this).

If you'd like to help us test and fine tune our thresholds for the moderation service, please join this test server: https://discord.gg/CWhsSgNdrP. You can let off some steam in a controlled environment ;)

To set a certain role immune to moderations, add the line `CHAT_BYPASS_ROLES="Role1,Role2,etc"` to your `.env file.

**The above server is NOT for support or discussions about GPT3Discord**

#How to Contribute
Click below to go to the contributing file.
[**Contributing**](https://github.com/Kav-K/GPT3Discord/tree/main/recent-updates-and-implementations)