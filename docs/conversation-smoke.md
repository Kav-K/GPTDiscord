# Live conversation smoke

Run `scripts/live_conversation_smoke.py` from a checkout with the core dependencies
installed. `--help` requires no credentials and makes no network calls. The script
is opt-in and is not collected by pytest or invoked by CI.

Supply `DISCORD_TOKEN` and `OPENAI_API_KEY` through your normal secret environment.
Keep the production `BOT_ACCESS_ROLE_IDS` value in that environment: the script
requires it and never replaces, removes, or broadens it. Do not put tokens in
command arguments or commit them. The chosen human member must currently hold
an exact configured role whose guild role name is `Maintainer`; membership and
role IDs are verified using fresh Discord REST responses. A restriction containing
the guild's `@everyone` role cannot exercise the denial check and is rejected.

```bash
python scripts/live_conversation_smoke.py --live \
  --guild-id GUILD_ID --channel-id TEXT_CHANNEL_ID --member-id MAINTAINER_MEMBER_ID \
  --model gpt-6.1-sol --report conversation-smoke-report.json
```

Replace the ID placeholders with positive numeric IDs. `--model` defaults to
`gpt-6.1-sol` and accepts a model or configured deployment ID. The normal OpenAI
base URL, organization, project, and proxy environment settings still apply.
The report path must not already exist. Bot permissions in the parent text channel
must include View Channel, Send Messages, Create Public Threads, Send Messages in
Threads, Read Message History, and Manage Threads for cleanup. If cleanup fails,
the report identifies the temporary thread and starter IDs for manual removal.

The default run first starts a separate plain `discord.Client` with the same bot
token, proxy settings, and `Intents.all()` used by production. This SDK-only client
loads no application modules, cogs, commands, or synchronization hooks. Within a
90-second preflight it requires the target guild to be present in the gateway's
guild cache, verifies the configured Maintainer using REST, posts one explicitly
temporary diagnostic message in the selected parent channel, and waits up to
12 seconds for that exact bot-authored `MESSAGE_CREATE` to return through the SDK's
message event. It records only READY/cache metadata and booleans, including whether
READY listed the target guild. An early gateway echo that arrives before the REST
send response is handled correctly. The diagnostic is deleted in `finally`, and
the SDK client closes before the hybrid checks start. Diagnostic deletion and
client shutdown have separate bounded cleanup timeouts.

A missing gateway guild or missing echo fails the run **before application
imports, thread creation, or paid provider calls**. REST guild/member reads can
succeed even when gateway delivery is broken; the later REST-backed checks alone
cannot establish gateway health. `gateway_tested` becomes true only after the
target guild, matching echo, and preflight cleanup succeed. This client has no
application callbacks and cannot duplicate another running bot's command handling.

After preflight, a second client logs in using REST without connecting the gateway,
fetches the actual guild, roles, channel and member, and creates one genuine public
thread attached to its own temporary starter. The real conversation start handler creates both the
starter and public thread through its normal `use_threads=True` branch. A small
context adapter supplies the REST-verified member and performs genuine channel
sends; only its slash acknowledgement methods are local no-ops. The runner then
exercises Pycord's real message create/update parsers and the registered
conversation listeners. These reach the normal `TextService`, model request builder, provider HTTP implementation,
and genuine Discord replies. It checks:

- The selected model is stored on the conversation and used for every request.
- A simulated role-denied create and edit add no provider calls or history.
- Original, genuinely bot-authored fixture messages are ignored before their
  authors are replaced inside the reconstructed human events.
- The second turn recalls a random word supplied only in the first turn. Its
  reconstructed human payload retains Discord's reply type (`19`) and a real
  message reference to the first bot answer; the edit retains that reply type.
- Editing the latest prompt retains earlier memory and replaces that turn's history.
- Awaiting-response state clears after each successful response.

The application portion is a **hybrid REST/parser smoke**. Bots cannot issue human slash commands.
The source fixtures are visibly bot-authored messages in Discord; their event
payloads are reconstructed as the freshly verified human member inside this
isolated process. The role-denial fixtures simulate removal of that member's roles
without changing their actual roles or the bot restriction. A real fixture message
ID allows genuine Discord reply references. The default preflight verifies the
SDK's gateway receipt of the bot's own message. Real human message delivery, slash
registration, interaction acknowledgement, and clicking views still need a separate
human-operated smoke; they remain marked untested in the report.

For targeted REST/parser debugging, add **`--rest-only`**. This explicitly skips the
gateway preflight, leaves `gateway_tested` false, and reports the narrower hybrid
scope. A passing `--rest-only` result does not establish that the deployed bot can
receive guild events or respond to human conversation messages.

Each run allows at most three OpenAI HTTP attempts, without automatic retries,
at most 512 output tokens per attempt, and no tools, images, embeddings or
moderation calls. Input messages are limited to 12,000 UTF-8 serialized bytes per
attempt and 30,000 bytes across the run, a conservative bound on text token volume.
Reasoning consumes the output budget and paid usage; a model that needs more than
512 tokens may fail this smoke without indicating a conversation regression.
Each provider call times out after 90 seconds and the hybrid smoke after eight
minutes, following the separate gateway preflight.

The runner skips optional providers and disables moderation only in its isolated
test process, disables dotenv loading, and puts settings, usage, and database files in
a private temporary directory that is removed on exit. It never starts the app's
ready handler, loads production pickles, or syncs commands. Only the separate
SDK-only preflight connects to the gateway.
Application logs are discarded because they may include prompts or response bodies.
The JSON report contains only check results, numeric usage, resource IDs, request
counts, selected model, and sanitized failure classifications. It contains no
prompts, generated answers, raw provider/Discord responses, credential values,
or provider exception text. It records hashes of the imported conversation cog,
text service, and model adapter, plus whether they came from the interpreter's
installation. These identify the application code actually exercised when
comparing an installed release with a checkout.

To test an installed release with the same runner, invoke its interpreter with
`-I` and add `--installed`. This skips checkout imports and verifies that all three
application modules reside under that interpreter's installation:

```bash
/path/to/release/venv/bin/python -I scripts/live_conversation_smoke.py --installed --live \
  --guild-id GUILD_ID --channel-id TEXT_CHANNEL_ID --member-id MAINTAINER_MEMBER_ID \
  --model gpt-6.1-sol --report installed-conversation-smoke-report.json
```

The hybrid portion populates the guild/thread cache from REST; the default
preflight requires the target guild to be present in an unmodified SDK gateway
cache first. Offline conversation-flow tests cover application parsing with a
missing guild cache. Run
`python -m pytest tests/test_conversation_flow.py -q` for the parser/listener
regression suite without credentials or network access.

A `finally` block deletes only the diagnostic message, thread, and starter created by this invocation,
after rechecking bot ownership and scope. It does not enumerate or clean up earlier
sessions. Exit code zero means all checks and cleanup passed. A failed run returns
one with a report; missing opt-in/configuration returns two without calls. Forced
process termination can interrupt cleanup; use the recorded resource IDs to remove
the runner's temporary resources manually.
