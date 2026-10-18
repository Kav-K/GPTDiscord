"""Reject text models whose documented API shutdown has already passed.

Snapshot verified on 2026-10-18 against OpenAI's deprecations page:
https://developers.openai.com/api/docs/deprecations
Upcoming deprecations and unknown custom deployment IDs remain usable.
"""

RETIRED_TEXT_MODELS = frozenset({
    "ada", "babbage", "curie", "davinci", "babbage-002", "davinci-002",
    "text-ada-001", "text-babbage-001", "text-curie-001",
    "text-davinci-001", "text-davinci-002", "text-davinci-003",
    "code-davinci-001", "code-davinci-002", "code-cushman-001", "code-cushman-002",
    "text-davinci-edit-001", "code-davinci-edit-001",
    "gpt-3.5-turbo-0301", "gpt-3.5-turbo-0613", "gpt-3.5-turbo-16k-0613",
    "gpt-3.5-turbo-1106", "gpt-3.5-turbo-instruct",
    "gpt-4-0314", "gpt-4-0125-preview", "gpt-4-turbo-preview",
    "gpt-4-turbo-preview-completions", "gpt-4-32k", "gpt-4-32k-0314",
    "gpt-4-32k-0613", "gpt-4-vision-preview", "gpt-4-1106-vision-preview",
    "gpt-4.5-preview", "gpt-4.5-preview-2025-02-27", "chatgpt-4o-latest",
    "o1-preview", "o1-preview-2024-09-12", "o1-mini", "o1-mini-2024-09-12",
    "computer-use-preview", "computer-use-preview-2025-03-11",
    "gpt-4o-mini-search-preview-2025-03-11", "gpt-4o-search-preview-2025-03-11",
    "gpt-5-chat-latest", "gpt-5-codex", "gpt-5.1-chat-latest",
    "gpt-5.1-codex", "gpt-5.1-codex-max", "gpt-5.1-codex-mini",
    "gpt-5.2-codex", "gpt-5.2-chat-latest", "gpt-5.3-chat-latest", "gpt-5.4-cyber",
    "o3-deep-research", "o3-deep-research-2025-06-26",
    "o4-mini-deep-research", "o4-mini-deep-research-2025-06-26",
})


def validate_text_model(model):
    """Reject known shutdowns without restricting current or custom deployments."""
    if model in RETIRED_TEXT_MODELS:
        raise ValueError("This OpenAI model has been retired. Choose a current model.")
    return model
