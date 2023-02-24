# Custom Indexes / Knowledgebase  
This bot supports per-user custom indexes. This means that users can upload files of their choosing, such as PDFs and ask GPT to answer questions based on those files. We also support using URLs for indexes.  
  
**This feature uses a large amount of tokens and money, and you should restrict it to trusted users.**  
  
Supported filetypes:  
- All text and data based files (PDF, TXT, DOCX, PPTX, CSV etc)  
- Images (JPG, PNG, etc) (Note: The bot will do OCR on the images to extract the text, this requires a lot of processing power sometimes)  
- Videos/Audio (MP4, MP3, etc) (Note: The bot will use OpenAI on the audio to extract the text, this requires a lot of processing power sometimes)  
- **Youtube Videos** - For all youtube videos that are transcribable, the bot will index the entire transcription of the given youtube video URL!  
  
Index Compositions:  
Indexes can be combined with other indexes through a composition. To combine indexes, you can run the `/index compose` command, and select the indexes that you want to combine together. You should only combine relevant indexes together, combining irrelevant indexes together will result in poor results (for example, don't upload a math textbook and then upload a large set of poems and combine them together). When creating a composition, you will be given the option to do a "Deep" composition, deep compositions are more detailed and will give you better results, but are incredibly costly and will sometimes take multiple minutes to compose.  
  
You can also compose a singular index with itself with "Deep Compose", this will give you a more detailed version of the index, but will be costly and will sometimes take multiple minutes to compose. **Deep compositions are useless for very short documents!**  

**When doing Deep Compositions, it's highly reccomended to keep the document size small, or only do deep compositions on single documents.** This is because a deep composition reorganizes the simple index into a tree structure and uses GPT3 to summarize different nodes of the tree, which will lead to high costs. For example, a deep composition of a 300 page lab manual and the contents of my personal website at https://kaveenk.com cost me $2 USD roughly.