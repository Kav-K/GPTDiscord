# Code Interpreter 
This bot supports a full fledged version of Code Interpreter, where code in various languages can be executed directly in discord. You can even install python and system packages. Python is the preferred language for code interpreter, although it will still work relatively nicely with other popular languages.

To get started with code interpreter, you need an E2B API key. You can find an E2B API key at https://e2b.dev/docs/getting-started/api-key
```env  
E2B_API_KEY="...."  
```  

Like above, add the E2B API key to your `.env` file. E2B is a cloud-based isolated execution environment, so that you can run code safely and have a containerized environment to install packages and etc in.
  
Afterwards, to use code interpreter, simply use `/code chat`.

When you begin a code interpreter instance, a new isolated environment to run your code is automatically created. Inside your chat, you can ask GPT to install python or system packages into this environment, and ask GPT to run any sort of python (and other language) code within it as well. Unlike ChatGPT's code interpreter / advanced data analysis, this also has access to the internet so you can work with code that uses the network as well.

When ChatGPT executes code, sometimes it will create files (especially if you ask it to). If files are created, they can be downloaded from the "Download Artifacts" button that will pop up after the code is executed.

Sometimes, ChatGPT will create files that are not in the upstream `artifacts` folder. To remedy this, simply ask it to ensure all files it makes are within `artifacts`. 