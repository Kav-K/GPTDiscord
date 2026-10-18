# Code Interpreter

`/code chat` runs Python in an E2B sandbox, with persistent variables and files for that session. The agent can also run shell commands and install Python or system packages.

Install the integration:

```sh
pip install '.[code]'
```

Configure `E2B_API_KEY` in the bot's environment, alongside its existing OpenAI configuration, and restart the bot. Obtain a key from the [E2B dashboard](https://e2b.dev/docs/getting-started/api-key). E2B compute and OpenAI model usage are billed by their respective providers.

Start `/code chat`, wait for the ready message, and ask it to execute code or analyze data. You can attach multiple files to a message; they are uploaded under `/home/user`. Python variables, installed packages, and files remain available for follow-up requests in that session. Shell commands run separately from the persistent Python kernel.

Ask the agent to save downloadable results under `/home/user/artifacts`. Generated plots are also made available there. Use **Download Artifacts** below a response to retrieve the files; each must fit the Discord server's upload limit. Failed commands return their exit code, stdout, and stderr so the agent can correct them.

Type `end` to terminate the sandbox and archive the thread. Files and Python state belong to the sandbox; download results you want to retain. The bot requests a one-hour sandbox lifetime, subject to E2B plan limits. Start a new `/code chat` after a sandbox expires or the bot restarts.

Failed startup and bot shutdown also close sandboxes. Operators using a service manager should allow a 120-second graceful stop window for pending startup and cleanup requests.
