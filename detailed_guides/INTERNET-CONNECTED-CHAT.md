# Internet-connected Chat

`/internet chat` uses OpenAI web search by default to answer questions with current sources and clickable citations. Follow-up questions keep context within the session, and the agent can read webpage links you provide.

Install the optional search integration:

```bash
python -m pip install '.[search]'
```

Configure `OPENAI_API_KEY` in your `.env`, then restart the bot. Your OpenAI project must support the selected model and Responses web search. No Google account or Google credentials are needed for the default provider. If the bot requires personal API keys, run `/setup` first.

Start `/internet chat`, optionally choose a supported model, and send your questions in the thread it creates. Type `end` to close the session.

`search_scope` limits OpenAI web tool calls per model response, not the number of sources or the total calls across a conversation turn. The agent may make multiple model requests when using tools. OpenAI search fees are separate from model token usage.

Google remains optional. Choose `/internet chat provider:google` after configuring `GOOGLE_SEARCH_API_KEY` and `GOOGLE_SEARCH_ENGINE_ID` as described in the [Google search setup guide](AI-SEARCH.md). With Google, `search_scope` limits result links per search. The separate `/internet search` command also requires those Google credentials.

For a one-off OpenAI web search, use `/web`; it works without the optional search dependencies.
