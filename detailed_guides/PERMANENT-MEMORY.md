# Permanent Memory and Conversations  
Permanent memory has now been implemented into the bot, using the OpenAI Ada embeddings endpoint, and <a href="https://www.pinecone.io/">Pinecone</a>.  
  
Pinecone is a vector database. The OpenAI Ada embeddings endpoint turns pieces of text into embeddings. The way that this feature works is by embedding the user prompts and the GPT responses, storing them in a pinecone index, and then retrieving the most relevant bits of conversation whenever a new user prompt is given in a conversation.  
  
**You do NOT need to use pinecone, if you do not define a `PINECONE_TOKEN` in your `.env` file, the bot will default to not using pinecone, and will use conversation summarization as the long term conversation method instead.**  
  
To enable permanent memory with pinecone, you must define a `PINECONE_TOKEN` in your `.env` file as follows (along with the other variables too):  
```env  
PINECONE_TOKEN="87juwi58-1jk9-9182-9b3c-f84d90e8bshq"  
```  
  
To get a pinecone token, you can sign up for a free pinecone account here: https://app.pinecone.io/ and click the "API Keys" section on the left navbar to find the key. (I am not affiliated with pinecone).  

Permanent memory using pinecone is still in alpha, I will be working on cleaning up this work, adding auto-clearing, and optimizing for stability and reliability, any help and feedback is appreciated (**add me on Discord Kaveen#0001 for pinecone help**)! If at any time you're having too many issues with pinecone, simply remove the `PINECONE_TOKEN` line in your `.env` file and the bot will revert to using conversation summarizations.  

Conversations persist even through bot restarts. Bot conversation data is stored locally in a folder called `pickles`. If you find your bot getting slow, delete this folder. A cleaner solution will be implemented in the future.

To manually create an index instead of the bot automatically doing it, go to the pinecone dashboard and click "Create Index" on the top right.  
  
<center><img src="https://i.imgur.com/L9LXVE0.png"/></center>  
  
Then, name the index `conversation-embeddings`, set the dimensions to `1536`, and set the metric to `DotProduct`:  
  
<center><img src="https://i.imgur.com/zoeLsrw.png"/></center> 