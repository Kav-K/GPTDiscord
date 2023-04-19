# Internet-connected Chat

Our bot supports an instance of ChatGPT 3.5/4 that is connected to the internet (google) and to Wolfram Alpha! 

This means that you can ask the bot questions about recent events, information found on the internet, and ask it to do complicated mathematical operations and it will be able to do so!

To get started with internet-connected-chat, you need to have entered your Google API key and google custom search engine ID into the .env file (https://github.com/Kav-K/GPT3Discord/blob/main/detailed_guides/AI-SEARCH.md).

To use Wolfram, you also need to ensure that you enter a `WOLFRAM_API_KEY` into your `.env` file. You need to sign up for a Wolfram Alpha developer account, the free tier for non-commercial use provides 2000 requests/month.



1\. Go to the Wolfram Developer Portal at https://products.wolframalpha.com/api/
----------------------------------------------------------------------


2\. Click on "Get API Access" under "Get Started For Free"
----------------------------------------

Click the "Get API Access Button":

![image](https://user-images.githubusercontent.com/23362597/232921927-67e6e967-01a4-4295-80d6-f955581d1ca4.png)


3\. Register yourself a new account
-----------------------------------

Follow the on-screen instructions to register a new account.

![image](https://user-images.githubusercontent.com/23362597/232921997-1c6ed4dc-7aea-459b-8d76-95fdf86ee108.png)


4\. Access the Wolfram Developer Portal
------------------------------

After successful registration, you will be redirected to the Developer Portal:

![image](https://user-images.githubusercontent.com/23362597/232922409-92af9237-1230-43dc-836a-7425f31b4f56.png)


5\. Click on "Get an AppID"
--------------------------

![image](https://user-images.githubusercontent.com/23362597/232922639-919f6d5a-da05-4f2c-af4b-5c517a926e78.png)

6\. Fill in a name and description:
-------------------------------------------------

![image](https://user-images.githubusercontent.com/23362597/232922798-25b21534-4452-4851-ae72-940b9f9d1fae.png)


8\. Copy the newly generated AppID to your ".env" file
------------------------------------

![image](https://user-images.githubusercontent.com/23362597/232922954-90dc2579-68f1-43fb-be89-d226f5336626.png)
