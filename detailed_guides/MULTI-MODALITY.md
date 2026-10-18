# Multi-Modality

Start `/gpt converse` with a vision-capable model and attach images to your messages. The bot sends the images and your text directly to the model's native image input. Your OpenAI project needs access to the selected model; image input incurs OpenAI usage charges.

Native OpenAI vision does not require Replicate or Google credentials. Use a model offered by the bot that supports image input; a text-only model cannot interpret attachments on its own.

## Optional fallback for text-only models

The separate Replicate integration can provide image captions and answers for models without native image input. Install `pip install '.[replicate]'` and set `REPLICATE_API_KEY` to enable it. Replicate charges separately; check its [current pricing](https://replicate.com/pricing).

Google Cloud Vision OCR is optional within that fallback. To include OCR, enable the Google Cloud Vision API and configure `GOOGLE_SEARCH_API_KEY`. These credentials are not needed for native OpenAI vision.
