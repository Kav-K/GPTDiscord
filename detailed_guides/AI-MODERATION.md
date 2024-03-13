# Automatic AI Moderation

`/mod set status:on` - Turn on automatic chat moderations.

`/mod set status:off` - Turn off automatic chat moderations.

`/mod set status:on alert_channel_id:<CHANNEL ID>` - Turn on moderations and set the alert channel to the channel ID you specify in the command.

## Moderation Service Configuration

You can choose between two moderation services: `OpenAI` and `PerspectiveAPI`. Each service has its own set of commands and thresholds for moderation.

**OpenAI Service:**
- `/mod config type:<warn/delete> hate:# hate_threatening:# self_harm:# sexual:# sexual_minors:# violence:# violence_graphic:#`
  - Configure the moderation thresholds using openai's content filter.
  - Example: `/mod config type:warn hate:0.2` sets the hate threshold for warnings.
  - Thresholds: Lower values are more strict, higher values are more lenient.

**PerspectiveAPI Service:**
- `/mod perspective_config toxicity:# severe_toxicity:# identity_attack:# insult:# profanity:# threat:# sexual_explicit:#`
  - Use this command to set thresholds using PerspectiveAPI's language analysis tools.
  - Example: `/mod perspective_config toxicity:0.7` sets the toxicity threshold for warnings.
  - Thresholds: Lower values are more strict, higher values are more lenient.

**Choosing the Moderation Service:**
- `MODERATION_SERVICE`: Set to either `openai` or `perspective`. Defaults to `openai`.

## Language Detection and Force Language Feature

Language detection is managed separately from the moderation service.
- `FORCE_LANGUAGE`: Set this to force the chat to speak in a specific language. Any messages that are not in the specified language will be deleted. Use a language code from the list below.
Supported languages include Arabic (ar), Chinese (zh), Czech (cs), Dutch (nl), English (en), French (fr), German (de), Hindi (hi), Hinglish (hi-Latn), Indonesian (id), Italian (it), Japanese (ja), Korean (ko), Polish (pl), Portuguese (pt), Russian (ru), Spanish (es), Swedish (sv).
- `LANGUAGE_DETECT_SERVICE`: This overrides the default language detection service. It can be set to either the `MODERATION_SERVICE` or a different one. Choose from `openai`, `perspective`. **Please note that `openai` only supports English and `perspective` supports all languages listed above.**
- `FORCE_ENGLISH`: An alias for setting `FORCE_LANGUAGE="en"`.

## Additional Configuration

- The bot requires Administrative permissions for full functionality.
- Set `MODERATIONS_ALERT_CHANNEL` in your `.env` file to the channel ID where you want to receive alerts about moderated messages.
- Requests to the moderation endpoint are sent at a MINIMUM gap of 0.5 seconds for reliability and to avoid blocking.
- To exempt certain roles from moderation, add `CHAT_BYPASS_ROLES="Role1,Role2,etc"` to your `.env` file.
- Enable pre-moderation for commands like /gpt ask, /gpt edit, /dalle draw, etc., with `PRE_MODERATE="True"` in the `.env` file. This will use `openai` no matter what the `MODERATION_SERVICE` is set to for the feature.
- `MAX_PERSPECTIVE_REQUESTS_PER_SECOND`: Adjust only if you receive a rate limit increase from Google.