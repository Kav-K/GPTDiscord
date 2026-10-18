## Language Detection

Set `FORCE_ENGLISH="True"` in your `.env` file to check messages longer than three words and delete those classified as non-English. Checks respect the bot's configured access restrictions.

This feature is in beta and may be buggy, so we're looking for your feedback on it to help us improve.

Language checks use the bot's current low-cost chat helper model. Your OpenAI project must have access to that model, and each check incurs API usage charges. Review current [OpenAI pricing](https://openai.com/api/pricing/) before enabling this for a busy server.
