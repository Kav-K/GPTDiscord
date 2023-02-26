# Requirements and Usage  
**For OCR, and document functionalities**:  
```  
pip3 install torch==1.9.1+cpu torchvision==0.10.1+cpu -f https://download.pytorch.org/whl/torch_stable.html  
```
OR  
```
python3.9 -m pip install torch==1.9.1+cpu torchvision==0.10.1+cpu -f https://download.pytorch.org/whl/torch_stable.html  
```  
**For audio extraction for indexing from .mp3 and .mp4 files**:  
```
python3.9 -m pip install git+https://github.com/openai/whisper.git
```
**All other dependencies**:  
```  
python3.9 -m pip install -r requirements.txt  
```
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
# Determines if the bot responds to messages that start with a mention of it
BOT_TAGGABLE = "true"
```  
  
# Installation  
  
### Create the bot  
  
https://discordpy.readthedocs.io/en/stable/discord.html  
  
- Create a new Bot on Discord Developer Portal:  
  - Applications -> New Application  
- Generate Token for the app (discord_bot_token)  
  - Select App (Bot) -> Bot -> Reset Token  
- Toggle PRESENCE INTENT:  
  - Select App (Bot) -> Bot -> PRESENCE INTENT, SERVER MEMBERS INTENT, MESSAGES INTENT, (basically turn on all intents)  
- Add Bot the server.  
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
```shell
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
```  
# First, navigate to the folder where the project files are 
screen -dmS GPTBot bash -c 'python3.9 gpt3discord.py'  
  
# Reattach to screen session  
screen -x # will reattach if this is the only screen session, if there are multiple, it will show IDs

# If there are multiple IDs returned by screen -x:  
screen -d -r {ID} # replace {ID} with the ID of the screen session you want to reattach to  
```  

As a last resort, you can try to run the bot using python in a basic way, with simply  
```  
cd GPT3Discord

python3.9 gpt3discord.py  
```
  
### Docker and Docker Compose :  

To use docker you can use the following command after [installing docker](https://docs.docker.com/get-docker/)
- Make a .env file to mount to `/opt/gpt3discord/etc/environment` in docker 
- `env_file` in the command should be replaced with where you have your .env file stored on your machine 
- Add `DATA_DIR=/data` to your env file -> `usage.txt` is saved here
- Add `SHARE_DIR=/data/share` to your env file -> this is where `conversation starters, optimizer pretext and the 'openers' folder` is alternatively loaded from for persistence
- Make sure the path on the left side of the colon in the paths below is a valid path on your machibne

```shell
docker run -d --name gpt3discord -v env_file:/opt/gpt3discord/etc/environment -v /containers/gpt3discord:/data -v /containers/gpt3discord/share:/data/share ghcr.io/kav-k/gpt3discord:main  
```  

If you wish to build your own image then do the following commands instead

```shell
# build the image
docker build --build-arg FULL=true -t gpt3discord .
# run it
docker run -d --name gpt3discord -v env_file:/opt/gpt3discord/etc/environment -v /containers/gpt3discord:/data -v /containers/gpt3discord/share:/data/share gpt3discord
```

Make sure all the paths are correct.  
  
  
#### Docker Compose   
To use Docker Compose, you need to have Docker and Docker Compose installed on your system. You can download and install them from the following links:  
  
- Docker  
- Docker Compose  
  
[You will need to install Docker for Desktop if you are on a desktop machine such as Windows or Mac, trying to run this]  
  
  
To start the gpt3discord container with Docker Compose, follow these steps:  
  
1. Rename the `sample.env` file to `.env` and fill it out
2. Open a terminal or command prompt and navigate to the directory that contains the docker-compose.yml file.
3. In the docker-compose.yml replace the volumes with a path on your machine if you don't use the ones listed, the path to replace is the one on the left side of the colon.
4. Run the following command to start the container in detached mode:  
  
```  
docker-compose up -d  
```  
  
This will start the container and use the settings in the docker-compose.yml file. The -d option tells Docker Compose to run the container in the background (detached mode).  
  
  
To stop the gpt3discord container, run the following command:  
  
```  
docker-compose down  
```  
  
This will stop the container and remove the services and networks defined in the docker-compose.yml file.  
  
That's it! With these simple steps, you can start and stop the gpt3discord container using Docker Compose.  
  
  
### Non-Server, Non-Docker installation (Windows included)  
  
You need to install python3.9 and pip for python3.9 on your system.  
  
With python3.9 installed and the requirements installed, you can run this bot anywhere.   
  
Install the dependencies with:
```
pip3 install torch==1.9.1+cpu torchvision==0.10.1+cpu -f https://download.pytorch.org/whl/torch_stable.html

python3.9 -m pip install -r requirements.txt  
```

Then, run the bot with:
```
python3.9 gpt3discord.py
```

Here's a great video from a community member that shows an installation on Windows: https://youtu.be/xLhwS2rQg14  
  
## Updating   
  
To update the bot, run (when working in the directory of GPT3discord):  
  
```
# To get the latest branch:  
git pull  
  
# Install the latest modules so the bot keeps working.  
python3.9 -m pip install -r requirements.txt

python3.9 -m pip install .  
```  
  
