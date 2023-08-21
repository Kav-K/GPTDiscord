![Docker](https://github.com/Kav-K/GPTDiscord/actions/workflows/build-and-publish-docker.yml/badge.svg)  
![PyPi](https://github.com/Kav-K/GPTDiscord/actions/workflows/pypi_upload.yml/badge.svg)  
![Build](https://github.com/Kav-K/GPTDiscord/actions/workflows/build.yml/badge.svg)  
  
[![PyPi version](https://badgen.net/pypi/v/gpt3discord/)](https://pypi.org/project/gpt3discord)  
[![Latest release](https://badgen.net/github/release/Kav-K/GPTDiscord)](https://github.com/Kav-K/GPTDiscord/releases)  
[![Maintenance](https://img.shields.io/badge/Maintained%3F-yes-green.svg)](https://GitHub.com/Kav-K/GPTDiscord/graphs/commit-activity)  
[![GitHub license](https://img.shields.io/github/license/Kav-K/GPTDiscord)](https://github.com/Kav-K/GPTDiscord/blob/master/LICENSE)  
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=flat-square)](http://makeapullrequest.com)  
  
# Overview
A robust, all-in-one GPT interface for Discord. Chat just like ChatGPT right inside Discord! Generate beautiful AI art using DALL-E 2! Automatically moderate your server using AI! Upload documents, videos, and files to get AI-assisted insights! A thorough integration with permanent conversation memory powered by [Pinecone](https://www.pinecone.io/), automatic request retry, fault tolerance and reliability for servers of any scale, and much more.  
  
SUPPORT SERVER FOR BOT SETUP: https://discord.gg/WvAHXDMS7Q

# Table of Contents  

- [Screenshots](#Screenshots)
- [Features](#Features)
- [Commands](#Commands)
- [Installation](https://github.com/Kav-K/GPTDiscord/blob/main/detailed_guides/INSTALLATION.md)  
-- [DigitalOcean Droplet Guide](https://github.com/Kav-K/GPTDiscord/blob/main/detailed_guides/DROPLET-GUIDE.md) 
-- [OpenAI Token Guide](https://github.com/Kav-K/GPTDiscord/blob/main/detailed_guides/OPENAI-GUIDE.md)
- [Internet Connected Chat](https://github.com/Kav-K/GPTDiscord/blob/main/detailed_guides/INTERNET-CONNECTED-CHAT.md)
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
<img src="https://i.imgur.com/LgJ58Ak.png"/><br>
Internet-connected chat (Google + Wolfram + Link Crawling)<br>
<img src="https://i.imgur.com/t9BDkJD.png"/><br>
Regular Chat <br>
<img src="https://i.imgur.com/KeLpDgj.png"/><br>
Image generation and optimization<br>
<img  src="https://i.imgur.com/jLp1T0h.png"/><br>
AI-based moderation<br>
<img src="https://i.imgur.com/cY4895V.png"/><br>
Custom indexing and Document Q&A<br>
<img src="https://i.imgur.com/9leCixJ.png"/><br>
</p>  
  
# Recent Notable Updates  

- **Multi-modality** - GPTDiscord now supports images sent to the bot during a conversation made with `/gpt converse`!
<p align="center"/>
<img src="https://i.imgur.com/OTJBm1W.png"/>
</p>

- **Internet-connected Chat!** - Chat with an instance of GPT3.5 or GPT-4 that's connected to google and wolfram alpha and can browse and access links that you send it!
<p align="center"/>
<img src="https://i.imgur.com/t9BDkJD.png"/>
</p>

# Features  
- **Directly prompt GPT with `/gpt ask <prompt>`**  
  
- **Have long term, permanent conversations with the bot, just like chatgpt, with `/gpt converse`** - Conversations happen in threads that get automatically cleaned up!  
  
- **Custom Indexes** - Use your own files, pdfs, txt files, websites, discord channel content as context when asking GPT questions!  
  
- **AI-Assisted Google Search** - Speaks for itself!  
  
- **DALL-E Image Generation** - Generate DALL-E AI images right in discord with `/dalle draw <prompt>`! It even supports multiple image qualities, multiple images, creating image variants, retrying, and saving images.  
  
- **DALL-E Image Prompt Optimization** - Given some text that you're trying to generate an image for, the bot will automatically optimize the text to be more DALL-E friendly! `/dalle optimize <prompt>`  
  
- **Edit Requests** - Ask GPT to edit a piece of text or code with a given instruction. `/gpt edit <instruction> <text>`  
  
- **DeepL Translations** - Translate text with DeepL. `/translate <text>`  
  
- **Redo Requests** - A simple button after the GPT response or DALL-E generation allows you to redo the initial prompt you asked. You can also redo conversation messages by just editing your message!  
  
- **Automatic AI-Based Server Moderation** - Moderate your server automatically with AI!  
  
- **Auto-retry on API errors** - Automatically resend failed requests to OpenAI's APIs!  

- Set context-based pre-instructions per-user and per-channel
  
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
  
### (Chat)GPT Commands  
  
`/gpt ask <prompt> <temp> <top_p> <frequency penalty> <presence penalty>` Ask the GPT Davinci 003 model a question. Optional overrides available  
  
`/gpt edit <instruction> <input> <temp> <top_p>` Use the bot to edit text using the given instructions for how to do it, currently an alpha openai feature so results might vary. Editing is currently free  
  
`/gpt converse <opener> <opener_file> <private> <minimal>` - Start a conversation with the bot, like ChatGPT. Also use the option `use_threads:False` to start a conversation in a full discord channel!
  
- `opener:<opener text>` - Start a conversation with the bot, with a custom opener text (this is useful if you want it to take on a custom personality from the start).  
  
- `opener_file:<opener file name>.txt|.json` - Starts a conversation with the bot, using a custom file.   
  
  - Loads files from the `/openers` folder, has autocomplete support so files in the folder will show up. Added before the `opener` as both can be used at the same time  
  
  - Custom openers need to be placed as a .txt file in the `openers` directory, in the same directory as `gpt3discord.py`  
  
 - Enables minimal  
  
  - Can use .json files in the `{"text": "your prompt", "temp":0, "top_p":0,"frequency_penalty":0,"presence_penalty":0}` format to include permanent overrides  
  
- `private` - Start a private conversation with the bot, like ChatGPT  
  
- `minimal` - Start a conversation with the bot, like ChatGPT, with minimal context (saves tokens)  
  
`/gpt end` - End a conversation with the bot.  

`/gpt instruction mode:<set/get/clear> type:<user/channel> <instruction> <instruction_file>` - The commands let you set a system instruction for 3.5-turbo and gpt4, or just prepending text for davinci and older models. Instruction will be added to prompts but not shown. The command has 3 options, `set` and `clear` can only be used with the `channel` type if the user has the `CHANNEL_INSTRUCTION_ROLES` role. User set instructions take priority, then channel instructions. This effect applies to `/gpt ask`, @ing the bot and the right click context menu options.

* set
  * Lets you set an instruction for yourself with `user` or the channel you run the command in with `channel`
  * `instruction` allows setting the text normally
  * `instruction_file` allows upload of a file, similar to the openers
  * When using both `instruction` and `instruction_file`, they will be combined with `instruction` appended after the `instruction_file`
* get
  * Prints the currently set instruction for either the `user` or `channel` it is ran in.
* clear
  * Removes the currently set instruction for either the `user` or `channel` it is ran in.
  
### DALL-E2 Commands  
  
`/dalle draw <prompt>` - Have DALL-E generate images based on a prompt  
  
`/dalle optimize <image prompt text>` Optimize a given prompt text for DALL-E image generation.  

### Search Commands

`/internet search:<prompt> scope:<number of sites to visit> nodes:<how deep gpt should think>` - Search the internet with GPT assistance!

- The `scope` defines how many top level websites to visit during the search, capped at 6
- `nodes` defines how many nodes inside the built index after webpage retrieval to use. 
- Increasing the scope or the nodes will make the requests take longer and will be more expensive, but will usually be more accurate.
  
`/internet chat search_scope:<number> model:<turbo or gpt4>` - Start an internet-connected chat with GPT, connected to Google and Wolfram.

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
  

# Step-by-Step Guides for GPTDiscord  
  
[**GPTDiscord Guides**](https://github.com/Kav-K/GPTDiscord/tree/main/detailed_guides)  
  
If you follow the link above, you will now get to detailed step-by-step guides that will help you to install and set up your GPTDiscord bot and its features quickly and easily. If you still run into problems or have suggestions for improving the guides, you can join the [**Discord-Server**](https://discord.gg/WvAHXDMS7Q) and we will try to help you. Keep in mind that the maintainers are volunteers and will try to help you on their schedule.  

  
*The number and content of the guides is constantly adapted to current requirements.*  
  
