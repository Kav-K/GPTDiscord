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

A server can have a correct bot installation and working application commands
while being present in application/REST responses and absent from gateway READY.
A missing gateway guild therefore requires checking both views of membership and
message delivery. Reinstalling the application does not establish that the gateway
now supplies its messages; use the preflight's cache and echo results to verify it.

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

## Production REST recovery

When a configured server remains absent from the gateway despite working REST
access, the bot can recover new conversation messages through bounded REST
polling. Enable it explicitly for selected servers:

```dotenv
CONVERSATION_RECOVERY_GUILD_IDS=YOUR_CONFIGURED_GUILD_ID
BOT_ACCESS_ROLE_IDS=YOUR_AUTHORIZED_ROLE_ID
```

Use positive numeric IDs. Recovery guilds must also appear in `ALLOWED_GUILDS`.
The recovery service requires the global role restriction and checks current
membership and exact role IDs through REST before allowing a speaker's message.
Administrator permissions, server ownership, and a cloned role name do not grant
access without a configured role ID. Existing feature-level role and personal API
key checks continue to apply. A failed membership lookup does not authorize a
provider request.

Recovery is limited to active public/private conversation threads owned by this
bot, present in its conversation registry, and associated with a configured guild
missing from the gateway. It does not scan arbitrary server channels or threads,
process direct messages, or turn REST membership into an entry in the gateway
cache. The normal conversation handler remains responsible for provider calls and
conversation memory.

The poll interval is nominally three seconds; Discord rate limits and provider
response time can add delay. Existing messages are skipped when recovery first
adopts an existing thread. Posts created after a newly started conversation can
be recovered. Polling initially handles message creates only. Edits still work
when Discord delivers their gateway events, but an edit with no gateway event is
not recovered; send a correction as a new message.

The service stores only thread/message IDs, cursors, and revision hashes in
`DATA_DIR/conversation_recovery.sqlite`. Gateway events and polling share the same
conversation lock and durable ingress claims. This prevents the same incoming
turn from being dispatched again through overlap or restart. An interruption
after the claim can lose an unanswered turn, and it is not automatically retried.
These are at-most-once ingress guarantees, not a promise of exactly-once OpenAI
billing or response delivery. Existing conversation pickles and model settings
are not migrated by this recovery store.

To exercise recovery with the installed application, keep both production
allowlists above in the environment and add `--rest-recovery`. The target must
already appear in `CONVERSATION_RECOVERY_GUILD_IDS`; this mode is mutually exclusive
with `--rest-only` and still requires `--live`:

```bash
/path/to/release/venv/bin/python -I scripts/live_conversation_smoke.py --installed --live --rest-recovery \
  --guild-id GUILD_ID --channel-id TEXT_CHANNEL_ID --member-id MAINTAINER_MEMBER_ID \
  --model gpt-6.1-sol --report recovery-conversation-smoke-report.json
```

This mode requires fresh SDK READY evidence that the target guild is missing,
then runs the real conversation setup and three new turns through recovery polling without
inserting that guild into the Gateway cache. Each genuine bot-authored fixture
must first be ignored. A test-local adapter changes only this run's registered
fixture IDs into simulated human messages using fresh REST membership; the
production recovery service performs its own role check. The three paid turns
check conversation memory, and repeated creates through REST and the Gateway
ingress add no requests. A successful report sets `recovery_transport_tested` to
true while `gateway_tested` stays false and `human_authorship_simulated` stays
true. Recovery does not test edit delivery, human Gateway delivery, slash-command
acknowledgements, or view callbacks. The same provider budget and owned-resource
cleanup rules apply; recovery source hashes are included in the report.
