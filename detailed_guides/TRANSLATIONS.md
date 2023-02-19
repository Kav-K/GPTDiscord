# Translations with DeepL  
This bot supports and uses DeepL for translations (optionally). If you want to enable the translations service, you can add a line in your `.env` file as follows:  
  
```  
DEEPL_TOKEN="your deepl token"  
```  
  
You can get a DeepL token by signing up at https://www.deepl.com/pro-api?cta=header-pro-api/ and clicking on the *free plan* to start. The DeepL translation service unlocks some new commands for your bot:  
  
`/translate <text> <language>` - Translate any given piece of text into the language that you provide  
  
`/languages` - See a list of all supported languages  
  
Using DeepL also adds a new app menu button (when you right click a message) to the bot which allows you to quickly translate any message in a channel into any language you want:  
  
<img src="https://i.imgur.com/MlNVWKu.png"/>  