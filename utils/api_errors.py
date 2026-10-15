"""Provider error bodies may contain account details or credential fragments."""


def openai_error_message(error):
    if error.status in (401, 403):
        return "OpenAI rejected the API key or project access. Check the configured key and model permissions."
    if error.status == 429:
        return "OpenAI's rate or spending limit was reached. Try again later or check billing."
    if error.status == 400:
        return "OpenAI rejected the request. Check the prompt, selected model, and API configuration."
    return "OpenAI could not complete the request. Please try again shortly."
