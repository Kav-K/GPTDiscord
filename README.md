<h1 align="center"> GPT Discord</h1>
<p align="center">An all-in-one GPT-3 interface for Discord. ChatGPT-style conversations, image generation, AI-moderation, custom indexes/knowledgebase, youtube summarizer, and more!</p>

[![Docker](https://github.com/Kav-K/GPTDiscord/actions/workflows/build-and-publish-docker.yml/badge.svg)](https://github.com/Kav-K/GPTDiscord/actions/workflows/build-and-publish-docker.yml)  
[![PyPi](https://github.com/Kav-K/GPTDiscord/actions/workflows/pypi_upload.yml/badge.svg)](https://github.com/Kav-K/GPTDiscord/actions/workflows/pypi_upload.yml)  
[![Build](https://github.com/Kav-K/GPTDiscord/actions/workflows/build.yml/badge.svg)](https://github.com/Kav-K/GPTDiscord/actions/workflows/build.yml)  
[![PyPi version](https://badgen.net/pypi/v/gpt3discord/)](https://pypi.org/project/gpt3discord)  
[![Latest release](https://badgen.net/github/release/Kav-K/GPTDiscord)](https://github.com/Kav-K/GPTDiscord/releases)  
[![Maintenance](https://img.shields.io/badge/Maintained%3F-yes-green.svg)](https://GitHub.com/Kav-K/GPTDiscord/graphs/commit-activity)  
[![GitHub license](https://img.shields.io/github/license/Kav-K/GPTDiscord)](https://github.com/Kav-K/GPTDiscord/blob/main/LICENSE)  
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=flat-square)](http://makeapullrequest.com)  

# Overview
An all-in-one, robust OpenAI Integration for Discord. This bot is on feature parity with ChatGPT web and even does some things slightly better! 

We support everything from **multi-modality image understanding**, **code interpretation**, advanced data analysis, Q&A on **your own documents**, **internet-connected chat** with Wolfram Alpha and Google access, **AI-moderation**, **image generation** with DALL-E, and much more! 

**BOT SETUP SUPPORT AND DEMO SERVER:** [Join Here](https://discord.gg/WvAHXDMS7Q)

Featuring code execution and environment manipulation by [E2B](https://e2b.dev)

We are migrating towards using [QDRANT](https://qdrant.tech/) as our vector database backing, we are moving away from pinecone.  

# Overview of Capabilities
![Overview of Features](https://i.imgur.com/BZdORTL.png)
# Table of Contents  

- [Screenshots](#Screenshots)
- [Features](#Features)
- [Commands](#Commands)
- [Installation](https://github.com/Kav-K/GPTDiscord/blob/main/detailed_guides/INSTALLATION.md)  
-- [DigitalOcean Droplet Guide](https://github.com/Kav-K/GPTDiscord/blob/main/detailed_guides/DROPLET-GUIDE.md) 
-- [OpenAI Token Guide](https://github.com/Kav-K/GPTDiscord/blob/main/detailed_guides/OPENAI-GUIDE.md)
- [Internet Connected Chat](https://github.com/Kav-K/GPTDiscord/blob/main/detailed_guides/INTERNET-CONNECTED-CHAT.md)
- [Code Interpreter / Advanced Data Analysis](https://github.com/Kav-K/GPTDiscord/blob/main/detailed_guides/CODE-INTERPRETER.md)
- [Permanent Memory](https://github.com/Kav-K/GPTDiscord/blob/main/detailed_guides/PERMANENT-MEMORY.md)    
- [Multi-Modality](https://github.com/Kav-K/GPTDiscord/blob/main/detailed_guides/MULTI-MODALITY.md)
- [AI-Search](https://github.com/Kav-K/GPTDiscord/blob/main/detailed_guides/AI-SEARCH.md)  
- [Custom Indexes](https://github.com/Kav-K/GPTDiscord/blob/main/detailed_guides/CUSTOM-INDEXES.md)  
- [AI-Moderation](https://github.com/Kav-K/GPTDiscord/blob/main/detailed_guides/AI-MODERATION.md)  
- [Translations](https://github.com/Kav-K/GPTDiscord/blob/main/detailed_guides/TRANSLATIONS.md)  
- [User-Input API Keys](https://github.com/Kav-K/GPTDiscord/blob/main/detailed_guides/USER-INPUT-KEYS.md)  
- [Permissions](https://github.com/Kav-K/GPTDiscord/blob/main/detailed_guides/PERMISSIONS.md)  
- [Language Detection](https://github.com/Kav-K/GPTDiscord/blob/main/detailed_guides/LANGUAGE-DETECTION.md)
- [Other Minor Features](https://github.com/Kav-K/GPTDiscord/blob/main/detailed_guides/OTHER-MINOR-FEATURES.md)  

# Screenshots
<p align="center">
Multi-Modality<br>
<img src="https://i.imgur.com/TsfgtU2.png"/><br>
Internet-connected chat (Google + Wolfram + Link Crawling)<br>
<img src="https://i.imgur.com/nHRNY2l.png"/><br>
Code Interpreter / Advanced Data Analysis <br>
<img src="https://i.imgur.com/Y2VvwHd.png"/><br>
Custom indexing and Document Q&A<br>
<img src="https://i.imgur.com/1uKF1ye.png"/><br>
</p>  

# Recent Notable Updates
- **Multi-modality + Drawing** - GPTDiscord now supports images sent to the bot during a conversation made with `/gpt converse`, and the bot can draw images for you and work with you on them!


- **GPT-4-Vision support, GPT-4-Turbo, DALLE-3 Support** - Assistant support also coming soon!


- **Code Interpreter / Advanced Data Analysis** - Just like ChatGPT, GPTDiscord now has a fully-fledged code execution environment. You can work with GPT to execute your code in an isolated environment, with the ability to even install Python and system packages, and access the internet from the execution environment.


- **Drag And Drop Document Chat** - Chat with your documents by simply dragging and dropping files, or even links into discord chat! `/index chat`


- **Internet-connected Chat!** - Chat with an instance of GPT3.5 or GPT-4 that's connected to Google and Wolfram Alpha and can browse and access links that you send it!

# Features
- **Multi-modal** with image understanding, you can generate images with DALL-E within multi-modal conversations!
- **Code Interpreter / Advanced Data Analysis**
- Long-term, **permanent conversations** with GPT models
- **Use your own files**, PDFs, text files, websites, Discord channel content as context when asking GPT questions!  
- **Internet-connected** chatting with GPT, connected to Google, Wolfram Alpha, and a web crawler
- Generate **DALL-E AI images** and even optimize them right in Discord
- **Translate** text with DeepL.
- Moderate your server automatically with AI!
- **Auto-retry on API errors** - Automatically resend failed requests silently to ensure a seamless experience
- Set context-based pre-instructions per user and per channel
- Ability to redo, edit your conversation messages while chatting with GPT
- ShareGPT integration to share your conversations
- Tag your bot in chat, and it'll respond!
- Async and fault-tolerant, **can handle hundreds of users at once**, if the upstream API permits!
- Change and view model parameters such as temperature, top_p, and more directly within Discord.
- Tracks token usage automatically
- Automatic pagination and Discord support. The bot will automatically send very long messages as multiple messages and is able to send Discord code blocks and emoji, gifs, etc.
- A low usage mode, use a command to automatically switch to a cheaper and faster model to conserve your tokens during times of peak usage.
- Prints debug to a channel of your choice, so you can view the raw response JSON
- Ability to specify a limit to how long a conversation can be with the bot, to conserve your tokens.

# Commands  
These commands are grouped, so each group has a prefix, but you can easily tab complete the command without the prefix. For example, for `/gpt ask`, if you type `/ask` and press tab, it'll show up too.

`/help` - Display help text for the bot  

### (Chat)GPT Commands  
- `/gpt ask <prompt> <temp> <top_p> <frequency penalty> <presence penalty>` Ask the GPT Davinci 003 model a question. Optional overrides available.
- `/gpt edit <instruction> <input> <temp> <top_p>` Use the bot to edit text using the given instructions for how to do it, currently an alpha OpenAI feature so results might vary. Editing is currently free.
- `/gpt converse <opener> <opener_file> <private> <minimal>` - Start a conversation with the bot, like ChatGPT. Also use the option `use_threads:False` to start a conversation in a full Discord channel!
- `/gpt end` - End a conversation with the bot.
- `/gpt instruction mode:<set/get/clear> type:<user/channel> <instruction> <instruction_file>` - The commands let you set a system instruction for 3.5-turbo and gpt4 or just prepending text for Davinci and older models.

### Code Interpreter // Advanced Data Analysis Commands  
- `/code chat` - Start a code interpreter chat with GPT. You can type `end` to end the conversation.

### Search & Internet Commands  
- `/internet search:<prompt> scope:<number of sites to visit> nodes:<how deep GPT should think>` - Search the internet with GPT assistance!
- `/internet chat search_scope:<number> model:<turbo or gpt4>` - Start an internet-connected chat with GPT, connected to Google and Wolfram.

### Custom Indexes Commands  
- `/index add file:<file> or link:<link>` - Use a document or use a link to create/add to your indexes.
- `/index query query:<prompt> nodes:<number> response_mode:<mode>` - Query your current index for a given prompt. GPT will answer based on your current document/index.
- `/index load user_index:<index> or server_index:<index>` - Load a previously created index you own yourself or an index for the whole server.
- `/index compose` - Combine multiple saved indexes into one or upgrade existing indexes into Deep Compositions.
- `/index reset` - Reset and delete all of your saved indexes.
- `/index add_discord channel:<discord channel>` - Create an add an index based on a Discord channel.
- `/index discord_backup` - Use the last 3000 messages of every channel on your Discord server as an index. Needs both an admin and an index role.
- `/index chat user_index:<user_index> search_index:<search_index>` - Chat with your documents that you've indexed previously!

### DALL-E2 Commands  
- `/dalle draw <prompt>` - Have DALL-E generate images based on a prompt.
- `/dalle optimize <image prompt text>` - Optimize a given prompt text for DALL-E image generation.

### System and Settings  
- `/system settings` - Display settings for the model (temperature, top_p, etc).
- `/system settings <setting> <value>` - Change a model setting to a new value. Has autocomplete support, certain settings will have autocompleted values too.
- `/system usage` - Estimate current usage details (based on Davinci).
- `/system settings low_usage_mode True/False` - Turn low usage mode on and off. If on, it will use the curie-001 model, and if off, it will use the Davinci-003 model.
- `/system delete-conversation-threads` - Delete all threads related to this bot across all servers.
- `/system local-size` - Get the size of the local dalleimages folder.
- `/system clear-local` - Clear all the local dalleimages.

# Step-by-Step Guides for GPTDiscord  
[**GPTDiscord Guides**](https://github.com/Kav-K/GPTDiscord/tree/main/detailed_guides)  
If you follow the link above, you will find detailed step-by-step guides that will help you install and set up your GPTDiscord bot and its features quickly and easily. If you encounter any issues or have suggestions for improving the guides, you can join the [**Discord Server**](https://discord.gg/WvAHXDMS7Q), and we will try to help you. Please keep in mind that the maintainers are volunteers and will try to assist you on their schedule.  
*The number and content of the guides are constantly adapted to current requirements.*

# Our Amazing Contributors ⭐  
[![Contributors](https://contrib.rocks/image?repo=Kav-K/GPTDiscord)](https://github.com/Kav-K/GPTDiscord)

Improve this markdown, correct any spelling errors, and let me know what you change.
