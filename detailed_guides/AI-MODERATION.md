### Automatic AI Moderation  
  
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
  