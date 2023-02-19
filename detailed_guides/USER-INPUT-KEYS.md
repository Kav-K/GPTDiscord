# User-Input API Keys (Multi-key tenancy)  
This bot supports multi-user tenancy in regards to API keys. This means that, if you wanted, you could make it such that each user needs to enter their own API key in order to use commands that use GPT3 and DALLE.  
  
To enable this, add the following line to the end of your `.env` file:  
```env  
USER_INPUT_API_KEYS="True"  
```  
  
Then, restart the bot, and it will set up the system for everyone to input their own API keys.   
  
The bot will use SQLite to store API keys for the users, each user's key will be saved with a USER_ID <> API_KEY mapping in SQLite, and will be persistent across restarts. All the data will be saved in a file called `user_key_db.sqlite` in the current working directory of the bot.  
  
With this feature enabled, any attempt to use a GPT3 or DALL-E command without a valid API key set for the user will pop up the following modal for them to enter their API key:  
  
<center><img src="https://i.imgur.com/ZDScoWk.png"/></center>  
  
Once the user enters their key, the bot will send a small test request to OpenAI to validate that the key indeed works, if not, it will tell the user to try again and tell them why it did not work.  
  
After the user's key is validated, they will be able to use GPT3 and DALLE commands.  
  
The Moderations service still uses the main API key defined in the `.env` file. Pinecone and discord-tokens are also per-host tokens, not per-user.  
  