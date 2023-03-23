## Other Features  
### Full-channel chat
This bot supports designating an entire discord text channel as a GPT conversation. Use /gpt converse and set the use_threads option to false. You can designate who is allowed to turn full channels into chat by setting the `CHANNEL_CHAT_ROLES` variable in your `.env` file to be a list of role names separated by commas that should be able to use this functionality.
### Health Check Service  
  
The bot has the ability to launch a HTTP endpoint at `<host>:8181/` that will return a json response of the bot's status and uptime. This is especially useful if you want to run this bot on cloud application containers, like Azure App Service.  
  
To enable this, add `HEALTH_SERVICE_ENABLED="True"` to your `.env` file.  
  
The health check endpoint will then be present in your bot's console when it is starting up, it will look like this, the possible HTTP urls for your health endpoint will be visible near the bottom:  
  
<center><img src="https://i.imgur.com/RqV2xN6.png"/></center>  
  
### Custom Bot Name  
Add a line `CUSTOM_BOT_NAME=<NAME>` to your `.env` to give your bot a custom name in conversations.  
  
### Permanent overrides in threads  
This bot now supports having overrides be permanent in an entire conversation if you use an opener file which includes them. The new opener files should be .json files formatted like this. `text` corresponds to what you want the conversational opener to be and the rest map 1:1 to the appropriate model settings. An example .json file is included by the name of `english_translator.json` in the `openers` folder  
```json  
{  
 "text": "your prompt",  "temp":0,   
  "top_p":0,  
 "frequency_penalty":0, "presence_penalty":0}  
```