# GPTDiscord

[![CI](https://github.com/Kav-K/GPTDiscord/actions/workflows/build.yml/badge.svg)](https://github.com/Kav-K/GPTDiscord/actions/workflows/build.yml)
[![Docker](https://github.com/Kav-K/GPTDiscord/actions/workflows/build-and-publish-docker.yml/badge.svg)](https://github.com/Kav-K/GPTDiscord/actions/workflows/build-and-publish-docker.yml)
[![PyPI](https://img.shields.io/pypi/v/gpt3discord)](https://pypi.org/project/gpt3discord/)

OpenAI chat, vision, image generation, transcription, moderation, and web search in Discord. Python **3.11–3.13** is supported.

## Current API support

- `/gpt ask` and `/gpt converse` use current Chat Completions models, including vision and reasoning models. Defaults and model limits are centralized; incompatible sampling options are omitted for reasoning models.
- `/web` uses the **Responses API with native web search** and visible source links for recent information. It needs an OpenAI key with access to the chosen model and web search, with no Google account required. Search tool calls incur additional OpenAI charges.
- `/dalle draw` keeps the familiar command name and generates images with the current **GPT Image** API. Image bytes are sent as Discord attachments. Retired DALL·E endpoints are no longer used.
- Transcription and moderation use current OpenAI APIs. Long conversations, per-user keys, server roles, prompt optimization, and saved conversations remain available.
- Document indexing, internet chat, Qdrant/Pinecone memory, Replicate, and E2B code execution are optional integrations with separate dependencies.

The default chat model is `gpt-6.1-sol`, the low-cost helper model is `gpt-6-luna`, and image generation defaults to `gpt-image-2.5-flare`. Availability depends on your OpenAI project. Override models using the environment settings below. Model names and prices change; consult the [OpenAI model catalog](https://developers.openai.com/api/docs/models) and [pricing](https://openai.com/api/pricing/).

## Run from source

```bash
git clone https://github.com/Kav-K/GPTDiscord.git
cd GPTDiscord
python3.13 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install .
cp sample.env .env
# Edit .env with your credentials and Discord server ID.
python gpt3discord.py
```

Install FFmpeg with your operating system's package manager to transcribe downloaded media. The basic bot does not require Torch, Transformers, LlamaIndex, or LangChain.

The same code installs as the `gpt3discord` console command. Prompt files and conversation openers ship in the wheel; you can run the installed bot outside the source directory.

### Required configuration

| Setting | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | OpenAI project API key. `OPENAI_TOKEN` remains a supported alias. ChatGPT subscriptions do not include API billing. |
| `DISCORD_TOKEN` | Bot token from the [Discord Developer Portal](https://discord.com/developers/applications). |
| `ALLOWED_GUILDS` | Comma-separated Discord server IDs where commands are registered. |
| `DATA_DIR` | Writable directory for local data and usage estimates. Defaults to the current directory. |

Enable the **Server Members** and **Message Content** privileged gateway intents for the Discord application; invite the bot with the `bot` and `applications.commands` OAuth scopes and permissions to read/send messages, attach files, embed links, and create/use threads. Review the role controls in `sample.env` before inviting it to a public server. Debug guild/channel IDs are optional.

Startup synchronizes commands only in `ALLOWED_GUILDS`, preserving global registrations and commands in other servers.

Keep credentials in an untracked `.env` or your hosting platform's secret store. GitHub credentials are unnecessary to run the basic bot or its tests. No passwords are required.

### Role restrictions

Set `BOT_ACCESS_ROLE_IDS` to a comma-separated list of Discord role IDs to restrict bot access. The restriction applies to commands, buttons, modal submissions, autocomplete, and messages processed by the bot. Members must hold one of those exact roles; administrator status and matching role names do not bypass it. Direct messages are denied while this restriction is enabled. Leave the setting unset to retain the existing per-feature role controls.

Per-feature settings such as `GPT_ROLES`, `ADMIN_ROLES`, and `SEARCH_ROLES` accept role IDs or case-insensitive names. Their checks still apply alongside the access restriction. To allow a restricted role to use every feature, put its ID in each applicable role setting as well. Role IDs continue to work when Discord's role-name cache is unavailable and remain valid after a role is renamed. Restart the bot after changing these settings.

### Conversation delivery recovery

Discord can provide working application commands and REST access while omitting a correctly installed server from the bot's gateway connection. Use the [conversation smoke guide](docs/conversation-smoke.md) to distinguish gateway delivery from REST-backed checks.

For those servers, opt in with `CONVERSATION_RECOVERY_GUILD_IDS`, a comma-separated subset of `ALLOWED_GUILDS`. Recovery also requires `BOT_ACCESS_ROLE_IDS`. It checks current member role IDs through REST and polls only explicitly registered conversations: bot-owned public/private threads, index/search/code chat threads, and full-channel conversations deliberately started with `/gpt converse use_threads:false`. Administrator status and matching role names do not bypass the configured role IDs. Free mentions, general moderation, and message edits still require Gateway events.

Recovery polls immediately on startup, then at a nominal three-second interval. An existing thread's first successful enrollment uses the latest message ID returned by Discord as its baseline. Messages at or before that baseline are skipped, so a post sent after startup but before enrollment may be skipped. Saved cursors keep their position, and new conversations start from their creation IDs. Polling does not recover undelivered edits; send corrections as new messages. Durable cursors in `DATA_DIR/conversation_recovery.sqlite` prevent the same incoming turn from being dispatched again by polling, gateway overlap, or restart. A failure after claiming a turn can leave it unanswered; this provides at-most-once ingress and does not guarantee exactly-once provider billing. See the guide for scope and verification details.

Recovery serves up to four sessions concurrently, keeping each session's messages in order. Full-channel sessions start at the latest message when enrolled, and starting a new session resets that baseline. Opening prompts, Retry, and End share the conversation lock and busy checks. Retry replaces the latest answer in memory as well as on Discord; older turns cannot replace newer conversation history. Private conversations require threads and explicitly add their owner. Conversations backed by Qdrant or Pinecone require corrections as new messages instead of Retry or message edits.

### Model and project settings

```dotenv
OPENAI_MODEL=gpt-6.1-sol
OPENAI_IMAGE_MODEL=gpt-image-2.5-flare
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
OPENAI_MODERATION_MODEL=omni-moderation-latest
OPENAI_TRANSCRIPTION_MODEL=gpt-4o-mini-transcribe
# OPENAI_PROJECT=proj_...
# OPENAI_ORGANIZATION=org_...
# OPENAI_BASE_URL=https://api.openai.com/v1
```

Automatic model selection follows `OPENAI_MODEL`, or `gpt-6.1-sol` when it is unset. A supported model saved through `/system settings` takes precedence across restarts; existing supported saved choices are preserved. Resetting all system settings or turning low-usage mode off resumes automatic selection.

`OPENAI_BASE_URL` supports compatible providers. Only configure endpoints you trust with your API credentials. Core chat keeps the Chat Completions response shape; `/web` requires a provider implementing Responses and native web search.

## Optional integrations

Install the extras you use and configure their keys. Missing configured dependencies produce an actionable startup error; disabled services respond with setup guidance.

| Feature | Install | Configuration |
| --- | --- | --- |
| Document Q&A / indexes | `pip install '.[index]'` | `ENABLE_INDEXING=true` and OpenAI access |
| Internet chat / Google search | `pip install '.[search]'` | OpenAI access for chat; Google keys only for `provider: google` or `/internet search` |
| Pinecone memory | `pip install '.[pinecone]'` | `PINECONE_TOKEN`, `PINECONE_REGION` (default `us-east-1`) |
| Qdrant memory | `pip install '.[qdrant]'` | `QDRANT_PATH` for persistent local storage, or `QDRANT_URL` and optional `QDRANT_API_KEY` |
| E2B code execution | `pip install '.[code]'` | `E2B_API_KEY` |
| Replicate fallback vision | `pip install '.[replicate]'` | `REPLICATE_API_KEY` |
| All integrations | `pip install '.[full]'` | Only configure the integrations you intend to enable |

Configure either Qdrant or Pinecone memory, never both. Leave both unset to keep local conversation summaries. See the [memory guide](detailed_guides/PERMANENT-MEMORY.md) for collection and migration details.

`/internet chat` defaults to **OpenAI web search** using your existing OpenAI key, with clickable citations, conversation memory, and optional page crawling. The search integration loads automatically when its dependencies are installed; Google credentials are not required for OpenAI chat. Choose `provider: google` to retain Google search (requires `GOOGLE_SEARCH_API_KEY` and `GOOGLE_SEARCH_ENGINE_ID`). `search_scope` caps OpenAI web tool calls per model response or Google result links per search; it does not guarantee a number of sources. OpenAI hosted-search fees are separate from model token usage. `/web` remains available without optional dependencies.

When `USER_INPUT_API_KEYS=true`, optional index/search/code sessions accept messages from their creator only, so other thread participants cannot spend that person's API credits. Changing or deleting the saved key blocks further work in an existing session; end it and start a new one with the current key.

The index extra includes GitHub and YouTube readers. GitHub repositories use `GITHUB_TOKEN` and their default branch. YouTube videos need an accessible English transcript. Public Google Docs links use text export; upload an exported document for private Docs. Uploaded images use native vision, while supported audio/video uploads use speech transcription before indexing. These media operations incur OpenAI charges; video frames are not indexed. Legacy Office files, SVG, and PSD require conversion first.

`/index add` selects the saved document for `/index query` and your next `/index chat`. Use `/index chat user_index:<name>` or `/index load` to choose another saved index. After a restart, chat defaults to your most recently saved index. The opening message identifies the connected source. Adding or loading documents inside your own chat connects them to that chat while preserving its existing documents and history. Empty chats ask you to connect a document before answering. PDF links must contain extractable text and be 25 MiB or smaller; scanned pages need OCR first.

LlamaIndex currently requires OpenAI SDK 2.x. Installing its extra selects the current compatible SDK; the core also supports SDK 3.x. Requirements files delegate to `pyproject.toml` so dependencies have one source of truth.

## Docker

Build this checkout with Docker Compose:

```bash
cp sample.env .env
# Edit .env, then:
docker compose up --build -d
docker compose logs -f
```

The container runs as UID/GID `10001`, includes FFmpeg, and persists data in a named volume. Existing bind-mounted data must be writable by this user. To include optional integrations, set the `FULL` build argument to `true` in Compose. The existing `/opt/gpt3discord/etc/environment` mount location remains supported.

CI builds both base and full images on pull requests. Registry publication is limited to releases or an explicit manual workflow and uses GitHub Container Registry authentication provided by Actions. PyPI publication uses `PYPI_TOKEN` when configured, with trusted publishing as the fallback when it is unset.

## Upgrade from v12

1. Back up `.env`, SQLite databases, pickles, local indexes, and stored vectors. Upgrade in a fresh Python 3.11–3.13 virtual environment; do not reuse the old pinned package set.
2. Install the core or the extras you use. Set `ENABLE_INDEXING=true` to enable document indexing. The basic bot and `/web` need no legacy search libraries.
3. Keep `OPENAI_TOKEN` or rename it to `OPENAI_API_KEY`. Review model overrides and role settings. Retired preview models saved in bot settings are migrated to current defaults.
4. Set `DATA_DIR` and, if previously used, `SHARE_DIR` to your existing writable data locations. Custom prompt/openers files can still be supplied through `SHARE_DIR`.
5. Rebuild document indexes when changing embedding models. **Ada and embedding-3 vectors occupy different vector spaces even at the same dimension.** New Pinecone embedding namespaces keep these separate; selecting `text-embedding-ada-002` retains the legacy namespace. Re-ingest historical text to use the newer embeddings. Legacy Pinecone numeric conversation IDs above `2**53` must also be re-ingested; new records store exact string IDs. Old local LlamaIndex storage may also need re-ingestion after the library upgrade.
6. Rebuild Docker images and check file ownership for existing volumes. Automatic deployment to the old hardcoded SSH host and formatter force-pushes have been removed. Configure deployment separately for your own host.

Conversation sharing now uses an owner-only private TXT download instead of the obsolete ShareGPT service. Retired `best_of`, `mode`, and `use_org` settings are no longer loaded or written. Old entries are ignored and removed when system settings are reset; conversations, indexes, and user API keys are preserved. API retirement decisions follow [OpenAI's API deprecations](https://developers.openai.com/api/docs/deprecations), reviewed on October 18, 2026.

Generated images offer per-image **Vary** controls using the current GPT Image edit API. This creates a source-guided variation preserving the subject and style; it differs from retired DALL·E random variation sampling. Retry retains the source image. There is no general upload/edit slash command.

Image sizes are `auto`, square `1024x1024`, landscape `1536x1024`, or portrait `1024x1536`. Saved `256x256` and `512x512` settings migrate to `1024x1024`.

The local usage total is an **estimate**, not an OpenAI invoice. Known text models use separate input, cached-input, and output token rates. Unknown models are logged and excluded rather than billed at an unrelated legacy rate. Image/audio/tool fees are not included in this token estimate; use your provider's usage dashboard for billing.

## Development and verification

See the [original-feature validation matrix](docs/feature-validation.md) for live and offline coverage, replaced features, and configuration limits.

```bash
python -m pip install -e '.[dev]'
python -m pip check
python -m ruff check .
python -m pytest -q -m 'not integration'
python -m build
python -m twine check dist/*
```

Normal tests mock OpenAI requests and require no credentials. Qdrant tests use real temporary local storage. At runtime, tiktoken downloads its vocabulary on first token counting; a populated `TIKTOKEN_CACHE_DIR` avoids that download. If network or cache access fails, core chat, document Q&A, tree/router composition, crawling, and optional chat memory use conservative UTF-8 byte counts until restart. API-reported usage still determines usage accounting. Paid integration tests only run when explicitly enabled:

```bash
RUN_LIVE_OPENAI_TESTS=1 python -m pytest -m integration
```

For broader live checks with `OPENAI_API_KEY` already in the environment, run `python -m tests.live_openai_smoke --run-live` from a fresh process. This makes at most one request per feature, caps text output at 512 tokens, and generates one low-quality image. Use `--features chat,web` to select checks; image/audio/tool charges still apply. The JSON report contains response shapes and usage counters, without credentials or generated content.

CI checks Python 3.11–3.13, Windows/macOS smoke tests, optional integration installs/imports, and wheel contents outside the checkout. Publishing and deployment credentials are not needed for pull requests.

Historical feature guides remain in [detailed_guides](detailed_guides/); their screenshots and setup details may describe older releases. Use this README and `sample.env` for current installation and API configuration. [Contributions](CONTRIBUTING.md) are welcome under the [MIT license](LICENSE).
