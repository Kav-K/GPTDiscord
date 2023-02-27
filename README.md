
![Docker](https://github.com/Kav-K/GPT3Discord/actions/workflows/build-and-publish-docker.yml/badge.svg)  
![PyPi](https://github.com/Kav-K/GPT3Discord/actions/workflows/pypi_upload.yml/badge.svg)  
![Build](https://github.com/Kav-K/GPT3Discord/actions/workflows/build.yml/badge.svg)  
  
[![PyPi version](https://badgen.net/pypi/v/gpt3discord/)](https://pypi.org/project/gpt3discord)  
[![Latest release](https://badgen.net/github/release/Kav-K/GPT3Discord)](https://github.com/Kav-K/GPT3Discord/releases)  
[![Maintenance](https://img.shields.io/badge/Maintained%3F-yes-green.svg)](https://GitHub.com/Kav-K/GPT3Discord/graphs/commit-activity)  
[![GitHub license](https://img.shields.io/github/license/Kav-K/GPT3Discord)](https://github.com/Kav-K/GPT3Discord/blob/master/LICENSE)  
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=flat-square)](http://makeapullrequest.com)  
  
# Overview
A robust, all-in-one GPT3 interface for Discord. Chat just like ChatGPT right inside Discord! Generate beautiful AI art using DALL-E 2! Automatically moderate your server using AI! Upload documents, videos, and files to get AI-assisted insights! A thorough integration with permanent conversation memory, automatic request retry, fault tolerance and reliability for servers of any scale, and much more.  
  
SUPPORT SERVER FOR BOT SETUP: https://discord.gg/WvAHXDMS7Q (You can try out the bot here also in a limited fashion)  

# Table of Contents  

- [Screenshots](#Screenshots)
- [Features](#Features)
- [Commands](#Commands)
- [Installation](https://github.com/Kav-K/GPT3Discord/blob/main/detailed_guides/INSTALLATION.md)  
-- [DigitalOcean Droplet Guide](https://github.com/Kav-K/GPT3Discord/blob/main/detailed_guides/DROPLET-GUIDE.md) 
-- [OpenAI Token Guide](https://github.com/Kav-K/GPT3Discord/blob/main/detailed_guides/OPENAI-GUIDE.md)
- [Permanent Memory](https://github.com/Kav-K/GPT3Discord/blob/main/detailed_guides/PERMANENT-MEMORY.md)    
- [AI-Search](https://github.com/Kav-K/GPT3Discord/blob/main/detailed_guides/AI-SEARCH.md)  
- [Custom Indexes](https://github.com/Kav-K/GPT3Discord/blob/main/detailed_guides/CUSTOM-INDEXES.md)  
- [AI-Moderation](https://github.com/Kav-K/GPT3Discord/blob/main/detailed_guides/AI-MODERATION.md)  
- [Translations](https://github.com/Kav-K/GPT3Discord/blob/main/detailed_guides/TRANSLATIONS.md)  
- [User-Input API Keys](https://github.com/Kav-K/GPT3Discord/blob/main/detailed_guides/USER-INPUT-KEYS.md)  
- [Permissions](https://github.com/Kav-K/GPT3Discord/blob/main/detailed_guides/PERMISSIONS.md)  
- [Language Detection](https://github.com/Kav-K/GPT3Discord/blob/main/detailed_guides/LANGUAGE-DETECTION.md)
- [Other Minor Features](https://github.com/Kav-K/GPT3Discord/blob/main/detailed_guides/OTHER-MINOR-FEATURES.md)  


# Screenshots  
  
<p align="center">  
<img src="https://i.imgur.com/KeLpDgj.png"/>  
<img  src="https://i.imgur.com/jLp1T0h.png"/>  
<img src="https://i.imgur.com/cY4895V.png"/>  
<img src="https://i.imgur.com/9leCixJ.png"/>  
  
</p>  
  
# Recent Notable Updates  
  
- **AI-Assisted Google Search** - Use GPT3 to browse the internet, you can search the internet for a query and GPT3 will look at the top websites for you automatically and formulate an answer to your query! You can also ask follow-up questions, this is kinda like BingGPT, but much better lol!  
<p align="center"/>  
<img src="https://i.imgur.com/YxkS0S5.png"/>  
</p>  
  
- **CUSTOM INDEXES** - You can now upload files to your discord server and use them as a source of knowledge when asking GPT3 questions. You can also use webpage links as context, images, full documents, csvs, powerpoints, audio files, and even **youtube videos**! Read more in the 'Custom Indexes' section below. Here's an example below with a youtube video:
  
<p align="center"/>  
<img src="https://i.imgur.com/H98UXad.png"/>  
</p>  

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
 
- ShareGPT integration to share your conversations
- Tag your bot in chat and it'll respond!  
- Async and fault tolerant, **can handle hundreds of users at once**, if the upstream API permits!  
- Change and view model parameters such as temp, top_p, and etc directly within discord.   
- Tracks token usage automatically  
- Automatic pagination and discord support, the bot will automatically send very long message as multiple messages, and is able to send discord code blocks and emoji, gifs, etc.  
- A low usage mode, use a command to automatically switch to a cheaper and faster model to conserve your tokens during times of peak usage.   
- Prints debug to a channel of your choice, so you can view the raw response JSON  
- Ability to specify a limit to how long a conversation can be with the bot, to conserve your tokens.  
  
# Commands  
  
These commands are grouped, so each group has a prefix but you can easily tab complete the command without the prefix. For example, for `/gpt ask`, if you type `/ask` and press tab, it'll show up too.  
  
`/help` - Display help text for the bot  
  
### (Chat)GPT3 Commands  
  
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
  
### DALL-E2 Commands  
  
`/dalle draw <prompt>` - Have DALL-E generate images based on a prompt  
  
`/dalle optimize <image prompt text>` Optimize a given prompt text for DALL-E image generation.  

### Search Commands

`/search query:<prompt> scope:<number of sites to visit> nodes:<how deep gpt3 should think>` - Search the internet with GPT3 assistance!

- The `scope` defines how many top level websites to visit during the search, capped at 6
- `nodes` defines how many nodes inside the built index after webpage retrieval to use. 
- Increasing the scope or the nodes will make the requests take longer and will be more expensive, but will usually be more accurate.
  
### Custom Indexes Commands  
  
This bot supports per-user custom indexes. This means that users can upload files of their choosing, such as PDFs and ask GPT to answer questions based on those files.  
  
`/index add file:<file> or link:<link>` - Use a document or use a link to create/add to your indexes. If you provide a youtube link, the transcript of the video will be used. If you provide a web url, the contents of the webpage will be used, if you provide an image, the image text will be extracted and used!  
  
`/index query query:<prompt> nodes:<number> response_mode:<mode>` - Query your current index for a given prompt. GPT will answer based on your current document/index. You can also set it to query over more nodes, further refining the output over each one. A description of the modes can be found <a href="https://gpt-index.readthedocs.io/en/latest/guides/usage_pattern.html#setting-response-mode">here</a>. They do not work for deep composed indexes  
  
`/index load user_index:<index> or server_index:<index>` - Load a previously created index you own yourself, or an index for the whole server.  
  
`/index compose` - Combine multiple saved indexes into one, or upgrade existing indexes into Deep Compositions.  
  
`/index reset` - Reset and delete all of your saved indexes  
  
`/index add_discord channel:<discord channel>` - Create an add an index based on a discord channel  
  
`/index discord_backup` - Use the last 3000 messages of every channel on your discord server as an index. Needs both an admin and a index role  
  
### System and Settings  
  
`/system settings` - Display settings for the model (temperature, top_p, etc)  
  
`/system settings <setting> <value>` - Change a model setting to a new value. Has autocomplete support, certain settings will have autocompleted values too.  

- For example, if I wanted to change the number of images generated by DALL-E by default to 4, I can type the following command in discord: `/system settings num_images 4`  
  
`/system usage` Estimate current usage details (based on davinci)  
  
`/system settings low_usage_mode True/False` Turn low usage mode on and off. If on, it will use the curie-001 model, and if off, it will use the davinci-003 model.  
  
`/system delete-conversation-threads` - Delete all threads related to this bot across all servers.  
  
`/system local-size` - Get the size of the local dalleimages folder  
  
`/system clear-local` - Clear all the local dalleimages.  
  

# Step-by-Step Guides for GPT3Discord  
  
[**GPT3Discord Guides**](https://github.com/Kav-K/GPT3Discord/tree/main/detailed_guides)  
  
If you follow the link above, you will now get to detailed step-by-step guides that will help you to install and set up your GPT3Discord bot and its features quickly and easily. If you still run into problems or have suggestions for improving the guides, you can join the [**Discord-Server**](https://discord.gg/WvAHXDMS7Q) and we will try to help you. Keep in mind that the maintainers are volunteers and will try to help you on their schedule.  
  
*The number and content of the guides is constantly adapted to current requirements.*  
  
