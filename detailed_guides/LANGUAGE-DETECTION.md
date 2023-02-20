## Language Detection

Using GPT, this bot can force everybody on your server to speak English. Simply add the environment variable `FORCE_ENGLISH="True"` to your `.env` file and the bot will automatically delete foreign language messages.

This feature is in beta and may be buggy, so we're looking for your feedback on it to help us improve.

This feature currently using `text-davinci-003`, so this is certainly an expensive feature to run if you have a lot of people in your server. Be careful.