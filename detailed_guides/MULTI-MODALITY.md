# Multi-Modality

This bot simulates GPT-4 multimodality by using a collection of services to obtain holistic image understanding during a conversation made with `/gpt converse`

For this functionality to work, you need a replicate account, and a corresponding replicate api token. You can sign up and get an api key at https://replicate.com/pricing. After getting the key, set `REPLICATE_API_KEY` in your environment file.

The cost to run replicate for image understanding is roughly $0.0032 per second, it will take on average 0.5-1.0 seconds per image. This is a small cost, but it will add up over time, so it's not recommended to release this feature to the public unless you're comfortable with it or have billing limits set on your replicate account.

As a second part of multi-modality, the bot will do OCR on uploaded images. You need to have the 'Google Cloud Vision' API enabled, and put your API key in the `GOOGLE_CLOUD_PROJECT_ID` field in your `.env` file for this to work.
