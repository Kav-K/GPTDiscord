# Installation

The [README](../README.md) contains the current installation instructions:

- [Run from source](../README.md#run-from-source), including supported Python versions and virtual environment setup.
- [Required configuration](../README.md#required-configuration), including credentials, server IDs, and data storage.
- [Optional integrations](../README.md#optional-integrations) and their dependencies.
- [Docker and Docker Compose](../README.md#docker).
- [Upgrade from an older installation](../README.md#upgrade-from-v12), including backups and existing data.

## Create the Discord bot

1. Create an application in the [Discord Developer Portal](https://discord.com/developers/applications), open its **Bot** page, and generate a bot token.
2. Enable the **Server Members** and **Message Content** privileged gateway intents.
3. Under **OAuth2 → URL Generator**, select the `bot` and `applications.commands` scopes. Choose the permissions described in the README's [required configuration](../README.md#required-configuration), open the generated URL, and invite the bot to your server.
4. Set `DISCORD_TOKEN` and `ALLOWED_GUILDS` using the README instructions. Enable Discord's **Developer Mode** to copy server and role IDs. Review the [role restrictions](../README.md#role-restrictions) before enabling access.

## Host the bot

You can run the bot on your own computer or a server from a provider such as DigitalOcean. Connect to a server through the provider's console or SSH, then follow the source or Docker instructions above. The [DigitalOcean guide](DROPLET-GUIDE.md) illustrates server provisioning; use the README for current runtime installation steps.

Use a service manager such as systemd, or run Docker Compose in detached mode, to keep the bot running after disconnecting. Set `DATA_DIR` and, if used, `SHARE_DIR` to persistent, writable storage so saved conversations and indexes survive restarts and upgrades.

## Audio and video

Transcription uses the hosted OpenAI API, with the model selected by `OPENAI_TRANSCRIPTION_MODEL`. A local Whisper package is unnecessary.

For audio/video indexing, install the optional `.[index]` integration described in the README and enable indexing. Install **FFmpeg** through your operating system's package manager to transcribe downloaded media. Transcription and indexing use your OpenAI API account.
