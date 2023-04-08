# AI-Assisted Google Search  
This bot supports searching google for answers to your questions with assistance from GPT! To get started, you need to get a Google Custom Search API key, and a Google Custom Search Engine ID. You can then define these as follows in your `.env` file:  
```env  
GOOGLE_SEARCH_API_KEY="...."  
GOOGLE_SEARCH_ENGINE_ID="...."  
```  
  
You first need to create a programmable search engine and get the search engine ID: https://developers.google.com/custom-search/docs/tutorial/creatingcse  
  
Then you can get the API key, click the "Get a key" button on this page: https://developers.google.com/custom-search/v1/introduction  

You can limit the max price that is charged for a single search request by setting `MAX_SEARCH_PRICE` in your `.env` file.