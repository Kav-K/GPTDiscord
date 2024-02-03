# AI-Assisted Google Search  
This bot supports searching google for answers to your questions with assistance from GPT! To get started, you need to get a Google Custom Search API key, and a Google Custom Search Engine ID. You can then define these as follows in your `.env` file:  
```env  
GOOGLE_SEARCH_API_KEY="...."  
GOOGLE_SEARCH_ENGINE_ID="...."  
```  
  
You first need to create a programmable search engine and get the search engine ID [here](https://developers.google.com/custom-search/docs/tutorial/creatingcse).  
  
Then you can get the API key, click the "Get a key" button [on this page](https://developers.google.com/custom-search/v1/introduction).  

You can limit the max price that is charged for a single search request by setting `MAX_SEARCH_PRICE` in your `.env` file.


Step by Step Guide:
---



1\. Go to the [Programmable Search Engine docs](https://developers.google.com/custom-search/docs/tutorial/creatingcse) to get a Search engine ID.
---

a. Click on "Control Panel" under "Defining a Programmable Engine in Control Panel"

b. Click to sign in(make a Google account if you do not have one):

![image](https://user-images.githubusercontent.com/23362597/233266042-98098ed5-72b2-41b3-9495-1a9f4d7e1101.png)


2\. Register yourself a new account/Login to the Control Panel
-----------------------------------

After logging in, you will be redirected to the Control Panel to create a new search engine:

![image](https://user-images.githubusercontent.com/23362597/233266323-53232468-2590-4820-b55f-08c78529d752.png)


3\. Create a new search engine
------------------------------

Fill in a name, select to "Search the entire web" and hit "Create":

![image](https://user-images.githubusercontent.com/23362597/233266738-b70f004d-4324-482e-a945-9b0193b60158.png)


4\. Copy your Search engine ID to your .env file
--------------------------

![image](https://user-images.githubusercontent.com/23362597/233267123-ea25a3bb-6cdb-4d46-a893-846ea4933632.png)


5\. Go to [custom-search docs](https://developers.google.com/custom-search/v1/introduction) to get a Google search API key
-------------------------------------------------

Click "Get a Key":

![image](https://user-images.githubusercontent.com/23362597/233267659-f82621f4-1f0b-46bf-8994-be443dd79932.png)


6\. Name your project and agree to the Terms of Service
------------------------------------

![image](https://user-images.githubusercontent.com/23362597/233267793-ca3c273d-ebc6-44a5-a49d-0d4c3223c992.png)


7\. Copy your Google search API key to your .env file
------------------------------------

![image](https://user-images.githubusercontent.com/23362597/233268067-5a6cfaf1-bec0-48b3-8add-70b218fb4264.png)


8\. Enable Cloud Vision API for image recognition
------------------------------------
a. Navigate to the Google Cloud API console [here](https://console.cloud.google.com/apis/api/vision.googleapis.com/).

b. Click on 'Create Project':

![image](https://github.com/Raecaug/GPTDiscord/assets/23362597/f128cc80-2a53-4578-9f4e-99791d7f8ffe)


c. Give it a name and create it:

![image](https://github.com/Raecaug/GPTDiscord/assets/23362597/35050805-f2ad-4489-8d8c-dacbd961c0b1)


d. Now, navigate to the API Library:

![image](https://github.com/Raecaug/GPTDiscord/assets/23362597/0eff23c1-09c1-4e65-a08a-a56af5515727)


e. Search for the 'Cloud Vision API' and enable it:

![image](https://github.com/Raecaug/GPTDiscord/assets/23362597/ef225dbe-4385-4263-b0aa-7100bbfed0e8)


9\. Enable Custom Search API for Google search
------------------------------------

You can follow [this link](https://console.cloud.google.com/apis/api/customsearch.googleapis.com/) to quickly jump to the page to enable the custom search API. You may need to first selet the project you created before:

![image](https://github.com/Raecaug/GPTDiscord/assets/23362597/875039dc-88b8-47a2-ad6e-4d39be26781d)

![image](https://github.com/Raecaug/GPTDiscord/assets/23362597/3a0f4200-7319-4a2f-8fb1-9d85bffb387a)

