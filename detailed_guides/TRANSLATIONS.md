# Translations with DeepL  
This bot supports and uses DeepL for translations (optionally). If you want to enable the translations service, you can add a line in your `.env` file as follows:  
  
```  
DEEPL_TOKEN="your deepl token"  
```  
  
The DeepL translation service unlocks some new commands for your bot:  
  
`/translate <text> <language>` - Translate any given piece of text into the language that you provide  
  
`/languages` - See a list of all supported languages  
  
Using DeepL also adds a new app menu button (when you right click a message) to the bot which allows you to quickly translate any message in a channel into any language you want:  
  
<img src="https://i.imgur.com/MlNVWKu.png"/>  



1\. Go to the [DeepL Translate API signup page](https://www.deepl.com/pro-api?cta=header-pro-api/) to get an API key
----------------------------------------------------------------------

Click "Sign up for free":

![image](https://user-images.githubusercontent.com/23362597/233269592-30e03ed8-36c6-4af4-bb42-aed0b04b3ef6.png)


2\. Click on "Sign up for free" under "DeepL API Free"
----------------------------------------

![image](https://user-images.githubusercontent.com/23362597/233269887-1fabb660-9060-4ade-aca2-4824190cbebe.png)


3\. Register yourself a new account/Login to the Account Overview
-----------------------------------

After logging in, you will be redirected to the Account Overview:

![image](https://user-images.githubusercontent.com/23362597/233270498-8a1e880c-0739-401d-a04c-7fae72ff0692.png)


4\. Copy Authentication Key to .env file
------------------------------

Click on the "Account" Tab on the Account Overview:

![image](https://user-images.githubusercontent.com/23362597/233270743-29ec4c19-0269-4cf1-8dc9-241583f0d95d.png)

Scroll down and copy your Authentication Key for DeepL API:

![image](https://user-images.githubusercontent.com/23362597/233270902-138cb082-4afa-4547-a97b-1c9cce7a23b2.png)

