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
- Document indexing, Google/Wolfram chat, Pinecone memory, Replicate, and E2B code execution are optional integrations with separate dependencies.

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

For those servers, opt in with `CONVERSATION_RECOVERY_GUILD_IDS`, a comma-separated subset of `ALLOWED_GUILDS`. Recovery also requires `BOT_ACCESS_ROLE_IDS`. It checks current member role IDs through REST and polls only active public/private conversation threads owned by this bot and recorded in its conversation registry. Administrator status and matching role names do not bypass the configured role IDs.

The nominal polling interval is three seconds. Recovery handles new messages, skips existing backlog when adopting a thread, and does not recover undelivered edits; send corrections as new messages. Durable cursors in `DATA_DIR/conversation_recovery.sqlite` prevent the same incoming turn from being dispatched again by polling, gateway overlap, or restart. A failure after claiming a turn can leave it unanswered; this provides at-most-once ingress and does not guarantee exactly-once provider billing. See the guide for scope and verification details.

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
| Google/Wolfram chat | `pip install '.[search]'` | `GOOGLE_SEARCH_API_KEY`, `GOOGLE_SEARCH_ENGINE_ID`; optionally `WOLFRAM_API_KEY` |
| Pinecone memory | `pip install '.[pinecone]'` | `PINECONE_TOKEN`, `PINECONE_REGION` (default `us-east-1`) |
| E2B code execution | `pip install '.[code]'` | `E2B_API_KEY` |
| Replicate fallback vision | `pip install '.[replicate]'` | `REPLICATE_API_KEY` |
| All integrations | `pip install '.[full]'` | Only configure the integrations you intend to enable |

When `USER_INPUT_API_KEYS=true`, optional index/search/code sessions accept messages from their creator only, so other thread participants cannot spend that person's API credits. Specialized GitHub, YouTube, and Google Docs readers may request an additional reader package when used.

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

CI builds both base and full images on pull requests. Registry publication is limited to releases or an explicit manual workflow and uses GitHub Container Registry authentication provided by Actions. PyPI publication uses a configured trusted publisher; see the release workflow for its required environment.

## Upgrade from v12

1. Back up `.env`, SQLite databases, pickles, local indexes, and stored vectors. Upgrade in a fresh Python 3.11–3.13 virtual environment; do not reuse the old pinned package set.
2. Install the core or the extras you use. Set `ENABLE_INDEXING=true` to enable document indexing. The basic bot and `/web` need no legacy search libraries.
3. Keep `OPENAI_TOKEN` or rename it to `OPENAI_API_KEY`. Review model overrides and role settings. Retired preview models saved in bot settings are migrated to current defaults.
4. Set `DATA_DIR` and, if previously used, `SHARE_DIR` to your existing writable data locations. Custom prompt/openers files can still be supplied through `SHARE_DIR`.
5. Rebuild document indexes when changing embedding models. **Ada and embedding-3 vectors occupy different vector spaces even at the same dimension.** New Pinecone embedding namespaces keep these separate; selecting `text-embedding-ada-002` retains the legacy namespace. Re-ingest historical text to use the newer embeddings. Old local LlamaIndex storage may also need re-ingestion after the library upgrade.
6. Rebuild Docker images and check file ownership for existing volumes. Automatic deployment to the old hardcoded SSH host and formatter force-pushes have been removed. Configure deployment separately for your own host.

Image variation commands for retired DALL·E models report that the operation is unavailable. Current image generation is supported; this release does not claim a general image-editing workflow.

The local usage total is an **estimate**, not an OpenAI invoice. Known text models use separate input, cached-input, and output token rates. Unknown models are logged and excluded rather than billed at an unrelated legacy rate. Image/audio/tool fees are not included in this token estimate; use your provider's usage dashboard for billing.

## Development and verification

```bash
python -m pip install -e '.[dev]'
python -m pip check
python -m ruff check .
python -m pytest -q -m 'not integration'
python -m build
python -m twine check dist/*
```

Normal tests mock OpenAI requests and require no credentials. At runtime, tiktoken downloads its vocabulary on first token counting; a populated `TIKTOKEN_CACHE_DIR` avoids that download. If network or cache access fails, local token estimates use conservative UTF-8 byte counts until restart, with one warning and no further download attempts. API-reported usage still determines usage accounting. Paid integration tests only run when explicitly enabled:

```bash
RUN_LIVE_OPENAI_TESTS=1 python -m pytest -m integration
```

For broader live checks with `OPENAI_API_KEY` already in the environment, run `python -m tests.live_openai_smoke --run-live` from a fresh process. This makes at most one request per feature, caps text output at 512 tokens, and generates one low-quality image. Use `--features chat,web` to select checks; image/audio/tool charges still apply. The JSON report contains response shapes and usage counters, without credentials or generated content.

CI checks Python 3.11–3.13, Windows/macOS smoke tests, optional integration installs/imports, and wheel contents outside the checkout. Publishing and deployment credentials are not needed for pull requests.

Historical feature guides remain in [detailed_guides](detailed_guides/); their screenshots and setup details may describe older releases. Use this README and `sample.env` for current installation and API configuration. [Contributions](CONTRIBUTING.md) are welcome under the [MIT license](LICENSE).
