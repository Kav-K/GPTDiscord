import asyncio
import base64
import io
import json
import math
import os
import re
import uuid

import aiohttp
import backoff
import discord

# An enum of two modes, TOP_P or TEMPERATURE
import requests
from services.environment_service import EnvService
from services.discord_attachment_service import read_attachment_bytes
from PIL import Image
from sqlitedict import SqliteDict

try:
    print("Attempting to retrieve the settings DB")
    SETTINGS_DB = SqliteDict(
        f"{EnvService.save_path()}/main_db.sqlite",
        tablename="settings",
        autocommit=True,
    )
    print("Retrieved the settings DB")
except Exception as e:
    print("Failed to retrieve the settings DB. The bot is terminating.")
    raise e


class Mode:
    TEMPERATURE = "temperature"
    TOP_P = "top_p"

    ALL_MODES = [TEMPERATURE, TOP_P]


class Override:
    def __init__(self, temp=None, top_p=None, frequency=None, presence=None):
        self.temperature = temp
        self.top_p = top_p
        self.frequency_penalty = frequency
        self.presence_penalty = presence


class Models:
    """Supported API model IDs and capabilities, separate from output budgets."""

    EMBEDDINGS = "text-embedding-3-small"
    MODERATION = "omni-moderation-latest"
    IMAGE = "gpt-image-2.5-flare"

    GPT_6_ASTRA = "gpt-6-astra"
    GPT_6_1_SOL = "gpt-6.1-sol"
    GPT_6_SOL = "gpt-6-sol"
    GPT_6_LUNA = "gpt-6-luna"
    GPT_5_5 = "gpt-5.5"
    GPT_5_4 = "gpt-5.4"
    GPT_5_4_MINI = "gpt-5.4-mini"
    GPT_5_4_NANO = "gpt-5.4-nano"
    GPT_5_2 = "gpt-5.2"
    GPT_5_1 = "gpt-5.1"
    GPT_5 = "gpt-5"
    GPT_5_MINI = "gpt-5-mini"
    GPT_5_NANO = "gpt-5-nano"
    GPT_4_1 = "gpt-4.1"
    GPT_4_1_MINI = "gpt-4.1-mini"
    GPT_4_1_NANO = "gpt-4.1-nano"
    GPT_4_OMEGA = "gpt-4o"
    GPT_4_OMEGA_MINI = "gpt-4o-mini"
    O3 = "o3"
    O4_MINI = "o4-mini"
    O1 = "o1"
    O3_MINI = "o3-mini"

    # Kept as aliases for integrations importing these names. Retired IDs are
    # deliberately excluded from slash-command choices and persisted settings.
    TURBO = "gpt-3.5-turbo"
    TURBO_16 = "gpt-3.5-turbo-16k"
    GPT4 = "gpt-4"
    GPT4_32 = "gpt-4-32k"
    GPT_4_TURBO = "gpt-4-1106-preview"
    GPT_4_TURBO_VISION = "gpt-4-vision-preview"
    GPT_4_TURBO_CATCHALL = "gpt-4-turbo-preview"
    GPT_4_TURBO_REGULAR = "gpt-4-turbo"
    O1_MINI = "o1-mini"

    TEXT_MODELS = [
        GPT_6_1_SOL,
        GPT_6_LUNA,
        GPT_6_ASTRA,
        GPT_6_SOL,
        GPT_5_5,
        GPT_5_4,
        GPT_5_4_MINI,
        GPT_5_4_NANO,
        GPT_5_2,
        GPT_5_1,
        GPT_5,
        GPT_5_MINI,
        GPT_5_NANO,
        GPT_4_1,
        GPT_4_1_MINI,
        GPT_4_OMEGA,
        GPT_4_OMEGA_MINI,
        O3,
    ]
    CHATGPT_MODELS = TEXT_MODELS.copy()
    GPT4_MODELS = TEXT_MODELS.copy()
    WEB_SEARCH_MODELS = [
        GPT_6_1_SOL,
        GPT_6_LUNA,
        GPT_6_ASTRA,
        GPT_6_SOL,
        GPT_5_5,
        GPT_5_4,
        GPT_5_4_MINI,
        GPT_5_4_NANO,
        GPT_5_2,
        GPT_5_1,
        GPT_4_1,
        GPT_4_1_MINI,
    ]
    DEFAULT = GPT_6_1_SOL
    LOW_USAGE_MODEL = GPT_6_LUNA

    TOKEN_MAPPING = {
        GPT_6_ASTRA: 1050000,
        GPT_6_1_SOL: 1050000,
        GPT_6_SOL: 1050000,
        GPT_6_LUNA: 1050000,
        GPT_5_5: 1050000,
        GPT_5_4: 1050000,
        GPT_5_4_MINI: 400000,
        GPT_5_4_NANO: 400000,
        GPT_5_2: 400000,
        GPT_5_1: 400000,
        GPT_5: 400000,
        GPT_5_MINI: 400000,
        GPT_5_NANO: 400000,
        GPT_4_1: 1047576,
        GPT_4_1_MINI: 1047576,
        GPT_4_1_NANO: 1047576,
        GPT_4_OMEGA: 128000,
        GPT_4_OMEGA_MINI: 128000,
        O3: 200000,
        O4_MINI: 200000,
        O1: 200000,
        O3_MINI: 200000,
        GPT_4_TURBO_REGULAR: 128000,
        TURBO: 16385,
        GPT4: 8192,
    }
    OUTPUT_TOKEN_MAPPING = {
        GPT_4_1: 32768,
        GPT_4_1_MINI: 32768,
        GPT_4_1_NANO: 32768,
        GPT_4_OMEGA: 16384,
        GPT_4_OMEGA_MINI: 16384,
        O3: 100000,
        O4_MINI: 100000,
        O1: 100000,
        O3_MINI: 100000,
        GPT_4_TURBO_REGULAR: 4096,
        TURBO: 4096,
        GPT4: 8192,
    }

    @staticmethod
    def get_max_tokens(model: str) -> int:
        """Return context capacity; never use this as the completion budget."""
        return Models.TOKEN_MAPPING.get(model, 128000)

    @staticmethod
    def get_max_output_tokens(model: str) -> int:
        return Models.OUTPUT_TOKEN_MAPPING.get(model, 128000)

    @staticmethod
    def is_reasoning(model: str) -> bool:
        return model.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4"))

    @staticmethod
    def supports_vision(model: str) -> bool:
        return model.startswith(("gpt-5", "gpt-6", "gpt-4.1", "gpt-4o")) or model in (
            Models.O1,
            Models.O3,
            Models.O4_MINI,
            Models.GPT_4_TURBO_REGULAR,
        )

    @staticmethod
    def token_parameter(model: str) -> str:
        return (
            "max_tokens"
            if model in (Models.TURBO, Models.GPT4, Models.GPT_4_TURBO_REGULAR)
            else "max_completion_tokens"
        )


class OpenAIAPIError(ValueError):
    """API failure whose status determines whether retrying can help."""

    def __init__(self, message, status, code=None):
        super().__init__(message)
        self.status = status
        self.code = code

    @property
    def retryable(self):
        if self.code in ("insufficient_quota", "billing_hard_limit_reached"):
            return False
        return self.status in (408, 409, 429) or self.status >= 500


class ImageSize:
    SMALL = "256x256"
    MEDIUM = "512x512"
    LARGE = "1024x1024"

    ALL_SIZES = [SMALL, MEDIUM, LARGE]


class ModelLimits:
    MIN_TOKENS = 15
    MAX_TOKENS = 128000

    MIN_CONVERSATION_LENGTH = 1
    MAX_CONVERSATION_LENGTH = 100000

    MIN_SUMMARIZE_THRESHOLD = 1500
    MAX_SUMMARIZE_THRESHOLD = 120000

    MIN_NUM_IMAGES = 1
    MAX_NUM_IMAGES = 4

    MIN_NUM_STATIC_CONVERSATION_ITEMS = 5
    MAX_NUM_STATIC_CONVERSATION_ITEMS = 20

    MIN_NUM_CONVERSATION_LOOKBACK = 5
    MAX_NUM_CONVERSATION_LOOKBACK = 15

    MIN_TEMPERATURE = 0.0
    MAX_TEMPERATURE = 2.0

    MIN_TOP_P = 0.0
    MAX_TOP_P = 1.0

    MIN_PRESENCE_PENALTY = -2.0
    MAX_PRESENCE_PENALTY = 2.0

    MIN_FREQUENCY_PENALTY = -2.0
    MAX_FREQUENCY_PENALTY = 2.0

    MIN_BEST_OF = 1
    MAX_BEST_OF = 3

    MIN_PROMPT_MIN_LENGTH = 5
    MAX_PROMPT_MIN_LENGTH = 4000


class Model:
    def set_initial_state(self, usage_service):
        self.mode = Mode.TEMPERATURE
        self.temp = (
            SETTINGS_DB["temp"] if "temp" in SETTINGS_DB else 0.85
        )  # Higher value means more random, lower value means more likely to be a coherent sentence
        self.top_p = (
            SETTINGS_DB["top_p"] if "top_p" in SETTINGS_DB else 1
        )  # 1 is equivalent to greedy sampling, 0.1 means that the model will only consider the top 10% of the probability distribution
        self.max_tokens = (
            SETTINGS_DB["max_tokens"] if "max_tokens" in SETTINGS_DB else 4000
        )  # The maximum number of tokens the model can generate
        self.presence_penalty = (
            SETTINGS_DB["presence_penalty"]
            if "presence_penalty" in SETTINGS_DB
            else 0.1
        )  # The presence penalty is a number between -2 and 2 that determines how much the model should avoid repeating the same text
        # Penalize new tokens based on their existing frequency in the text so far. (Higher frequency = lower probability of being chosen.)
        self.frequency_penalty = (
            SETTINGS_DB["frequency_penalty"]
            if "frequency_penalty" in SETTINGS_DB
            else 0.0
        )
        self.best_of = (
            SETTINGS_DB["best_of"] if "best_of" in SETTINGS_DB else 1
        )  # Number of responses to compare the loglikelihoods of
        self.prompt_min_length = (
            SETTINGS_DB["prompt_min_length"]
            if "prompt_min_length" in SETTINGS_DB
            else 6
        )  # The minimum length of the prompt
        self.max_conversation_length = (
            SETTINGS_DB["max_conversation_length"]
            if "max_conversation_length" in SETTINGS_DB
            else 100000
        )  # The maximum number of conversation items to keep in memory
        configured_model = EnvService.get_openai_model()
        saved_model = SETTINGS_DB.get("model")
        if saved_model in Models.TEXT_MODELS or (
            configured_model is not None and saved_model == configured_model
        ):
            # Keep supported legacy choices: old databases cannot distinguish
            # an administrator's selection from a startup-written default.
            self._apply_model(saved_model)
        else:
            # Only explicit selections belong in persisted model settings.
            # Fresh/reset installations should follow configuration and future
            # catalog defaults instead of pinning their first startup's model.
            if "model" in SETTINGS_DB:
                del SETTINGS_DB["model"]
            self._apply_model(configured_model or Models.DEFAULT)
        self._low_usage_mode = False
        self.usage_service = usage_service
        self.DAVINCI_ROLES = ["admin", "Admin", "GPT", "gpt"]
        self.image_size = (
            SETTINGS_DB["image_size"]
            if "image_size" in SETTINGS_DB
            else ImageSize.MEDIUM
        )
        self.num_images = (
            SETTINGS_DB["num_images"] if "num_images" in SETTINGS_DB else 2
        )
        self.summarize_conversations = (
            bool(SETTINGS_DB["summarize_conversations"])
            if "summarize_conversations" in SETTINGS_DB
            else True
        )
        self.summarize_threshold = (
            SETTINGS_DB["summarize_threshold"]
            if "summarize_threshold" in SETTINGS_DB
            else 5000
        )
        self.model_max_tokens = 4024
        self.welcome_message_enabled = (
            bool(SETTINGS_DB["welcome_message_enabled"])
            if "welcome_message_enabled" in SETTINGS_DB
            else False
        )
        self.num_static_conversation_items = (
            SETTINGS_DB["num_static_conversation_items"]
            if "num_static_conversation_items" in SETTINGS_DB
            else 10
        )
        self.num_conversation_lookback = (
            SETTINGS_DB["num_conversation_lookback"]
            if "num_conversation_lookback" in SETTINGS_DB
            else 5
        )
        self.use_org = (
            bool(SETTINGS_DB["use_org"]) if "use_org" in SETTINGS_DB else False
        )

    def reset_settings(self):
        keys = [
            "temp",
            "top_p",
            "max_tokens",
            "presence_penalty",
            "frequency_penalty",
            "best_of",
            "prompt_min_length",
            "max_conversation_length",
            "model",
            "image_size",
            "num_images",
            "summarize_conversations",
            "summarize_threshold",
            "welcome_message_enabled",
            "num_static_conversation_items",
            "num_conversation_lookback",
            "use_org",
        ]
        for key in keys:
            try:
                del SETTINGS_DB[key]
            except:
                pass
        self.set_initial_state(self.usage_service)

    def __init__(self, usage_service):
        self._num_conversation_lookback = None
        self._num_static_conversation_items = None
        self._welcome_message_enabled = None
        self.model_max_tokens = None
        self._summarize_threshold = None
        self._summarize_conversations = None
        self._num_images = None
        self._image_size = None
        self.DAVINCI_ROLES = None
        self.usage_service = None
        self._low_usage_mode = None
        self._model = None
        self._max_conversation_length = None
        self._prompt_min_length = None
        self._best_of = None
        self._frequency_penalty = None
        self._presence_penalty = None
        self._max_tokens = None
        self._top_p = None
        self._temp = None
        self._mode = None
        self._use_org = None
        self.set_initial_state(usage_service)

        try:
            self.IMAGE_SAVE_PATH = os.environ["IMAGE_SAVE_PATH"]
            self.custom_image_path = True
        except Exception:
            self.IMAGE_SAVE_PATH = "dalleimages"
            # Try to make this folder called images/ in the local directory if it doesnt exist
            if not os.path.exists(self.IMAGE_SAVE_PATH):
                os.makedirs(self.IMAGE_SAVE_PATH)
            self.custom_image_path = False

        self._hidden_attributes = [
            "usage_service",
            "DAVINCI_ROLES",
            "custom_image_path",
            "custom_web_root",
            "_hidden_attributes",
            "model_max_tokens",
            "openai_key",
            "openai_organization",
            "IMAGE_SAVE_PATH",
            "openai_project",
            "openai_base_url",
        ]

        self.openai_key = EnvService.get_openai_token()
        self.openai_organization = EnvService.get_openai_organization()
        self.openai_project = EnvService.get_openai_project()
        self.openai_base_url = EnvService.get_openai_base_url().rstrip("/")

    # Use the @property and @setter decorators for all the self fields to provide value checking

    @property
    def use_org(self):
        return self._use_org

    @use_org.setter
    def use_org(self, value):
        self._use_org = value
        SETTINGS_DB["use_org"] = value

    @property
    def num_static_conversation_items(self):
        return self._num_static_conversation_items

    @num_static_conversation_items.setter
    def num_static_conversation_items(self, value):
        value = int(value)
        if value < ModelLimits.MIN_NUM_STATIC_CONVERSATION_ITEMS:
            raise ValueError(
                f"Number of static conversation items must be >= {ModelLimits.MIN_NUM_STATIC_CONVERSATION_ITEMS}"
            )
        if value > ModelLimits.MAX_NUM_STATIC_CONVERSATION_ITEMS:
            raise ValueError(
                f"Number of static conversation items must be <= {ModelLimits.MAX_NUM_STATIC_CONVERSATION_ITEMS}, this is to ensure reliability and reduce token wastage!"
            )
        self._num_static_conversation_items = value
        SETTINGS_DB["num_static_conversation_items"] = value

    @property
    def num_conversation_lookback(self):
        return self._num_conversation_lookback

    @num_conversation_lookback.setter
    def num_conversation_lookback(self, value):
        value = int(value)
        if value < ModelLimits.MIN_NUM_CONVERSATION_LOOKBACK:
            raise ValueError(
                f"Number of conversations to look back on must be >= {ModelLimits.MIN_NUM_CONVERSATION_LOOKBACK}"
            )
        if value > ModelLimits.MAX_NUM_CONVERSATION_LOOKBACK:
            raise ValueError(
                f"Number of conversations to look back on must be <= {ModelLimits.MIN_NUM_CONVERSATION_LOOKBACK}, this is to ensure reliability and reduce token wastage!"
            )
        self._num_conversation_lookback = value
        SETTINGS_DB["num_conversation_lookback"] = value

    @property
    def welcome_message_enabled(self):
        return self._welcome_message_enabled

    @welcome_message_enabled.setter
    def welcome_message_enabled(self, value):
        if not isinstance(value, bool):
            if value.lower() == "true":
                value = True
            elif value.lower() == "false":
                value = False
            else:
                raise ValueError("Value must be either `true` or `false`!")
        self._welcome_message_enabled = value
        SETTINGS_DB["welcome_message_enabled"] = self._welcome_message_enabled

    @property
    def summarize_threshold(self):
        return self._summarize_threshold

    @summarize_threshold.setter
    def summarize_threshold(self, value):
        value = int(value)
        if (
            value < ModelLimits.MIN_SUMMARIZE_THRESHOLD
            or value > ModelLimits.MAX_SUMMARIZE_THRESHOLD
        ):
            raise ValueError(
                f"Summarize threshold should be a number between {ModelLimits.MIN_SUMMARIZE_THRESHOLD} and {ModelLimits.MAX_SUMMARIZE_THRESHOLD}!"
            )
        self._summarize_threshold = value
        SETTINGS_DB["summarize_threshold"] = value

    @property
    def summarize_conversations(self):
        return self._summarize_conversations

    @summarize_conversations.setter
    def summarize_conversations(self, value):
        # convert value string into boolean
        if not isinstance(value, bool):
            if value.lower() == "true":
                value = True
            elif value.lower() == "false":
                value = False
            else:
                raise ValueError("Value must be either `true` or `false`!")
        self._summarize_conversations = value
        SETTINGS_DB["summarize_conversations"] = value

    @property
    def image_size(self):
        return self._image_size

    @image_size.setter
    def image_size(self, value):
        if value in ImageSize.ALL_SIZES:
            self._image_size = value
            SETTINGS_DB["image_size"] = value
        else:
            raise ValueError(
                f"Image size must be one of the following: {ImageSize.ALL_SIZES}"
            )

    @property
    def num_images(self):
        return self._num_images

    @num_images.setter
    def num_images(self, value):
        value = int(value)
        if value < ModelLimits.MIN_NUM_IMAGES or value > ModelLimits.MAX_NUM_IMAGES:
            raise ValueError(
                f"Number of images to generate should be a number between {ModelLimits.MIN_NUM_IMAGES} and {ModelLimits.MAX_NUM_IMAGES}!"
            )
        self._num_images = value
        SETTINGS_DB["num_images"] = value

    @property
    def low_usage_mode(self):
        return self._low_usage_mode

    @low_usage_mode.setter
    def low_usage_mode(self, value):
        if not isinstance(value, bool):
            if str(value).lower() not in ("true", "false"):
                raise ValueError("Value must be either `true` or `false`!")
            value = str(value).lower() == "true"
        self._low_usage_mode = value
        if value:
            self.model = Models.LOW_USAGE_MODEL
        else:
            self._apply_model(EnvService.get_openai_model() or Models.DEFAULT)
            SETTINGS_DB.pop("model", None)
        self.max_tokens = 1900 if value else 4000
        self.model_max_tokens = self.max_tokens

    @property
    def model(self):
        return self._model

    @model.setter
    def model(self, model):
        self._apply_model(model)
        SETTINGS_DB["model"] = model

    def _apply_model(self, model):
        """Apply capabilities without turning a startup default into a choice."""
        if model not in Models.TEXT_MODELS and model != EnvService.get_openai_model():
            raise ValueError(f"Invalid model, must be one of: {Models.TEXT_MODELS}")
        self._model = model

        # Set the token count
        self._max_tokens = min(
            self._max_tokens or 4000, Models.get_max_output_tokens(model)
        )
        self._summarize_threshold = min(
            ModelLimits.MAX_SUMMARIZE_THRESHOLD,
            max(
                ModelLimits.MIN_SUMMARIZE_THRESHOLD,
                Models.get_max_tokens(model) - self._max_tokens - 1000,
            ),
        )

    @property
    def max_conversation_length(self):
        return self._max_conversation_length

    @max_conversation_length.setter
    def max_conversation_length(self, value):
        value = int(value)
        if value < ModelLimits.MIN_CONVERSATION_LENGTH:
            raise ValueError(
                f"Max conversation length must be greater than {ModelLimits.MIN_CONVERSATION_LENGTH}"
            )
        if value > ModelLimits.MAX_CONVERSATION_LENGTH:
            raise ValueError(
                f"Max conversation length must be less than {ModelLimits.MIN_CONVERSATION_LENGTH}, this will start using credits quick."
            )
        self._max_conversation_length = value
        SETTINGS_DB["max_conversation_length"] = value

    @property
    def mode(self):
        return self._mode

    @mode.setter
    def mode(self, value):
        if value not in Mode.ALL_MODES:
            raise ValueError(f"Mode must be one of: {Mode.ALL_MODES}")

        # Set the other mode to 1 (the default) so that it is not used
        # See https://beta.openai.com/docs/api-reference/completions/create#completions/create-temperature
        if value == Mode.TOP_P:
            self._temp = 1
        elif value == Mode.TEMPERATURE:
            self._top_p = 1
        else:
            raise ValueError(f"Unknown mode: {value}")

        self._mode = value
        SETTINGS_DB["mode"] = value

    @property
    def temp(self):
        return self._temp

    @temp.setter
    def temp(self, value):
        value = float(value)
        if value < ModelLimits.MIN_TEMPERATURE or value > ModelLimits.MAX_TEMPERATURE:
            raise ValueError(
                f"Temperature must be between {ModelLimits.MIN_TEMPERATURE} and {ModelLimits.MAX_TEMPERATURE}, it is currently: {value}"
            )

        self._temp = value
        SETTINGS_DB["temp"] = value

    @property
    def top_p(self):
        return self._top_p

    @top_p.setter
    def top_p(self, value):
        value = float(value)
        if value < ModelLimits.MIN_TOP_P or value > ModelLimits.MAX_TOP_P:
            raise ValueError(
                f"Top P must be between {ModelLimits.MIN_TOP_P} and {ModelLimits.MAX_TOP_P}, it is currently: {value}"
            )
        self._top_p = value
        SETTINGS_DB["top_p"] = value

    @property
    def max_tokens(self):
        return self._max_tokens

    @max_tokens.setter
    def max_tokens(self, value):
        value = int(value)
        if value < ModelLimits.MIN_TOKENS or value > ModelLimits.MAX_TOKENS:
            raise ValueError(
                f"Max tokens must be between {ModelLimits.MIN_TOKENS} and {ModelLimits.MAX_TOKENS}, it is currently: {value}"
            )
        self._max_tokens = value
        SETTINGS_DB["max_tokens"] = value

    @property
    def presence_penalty(self):
        return self._presence_penalty

    @presence_penalty.setter
    def presence_penalty(self, value):
        value = float(value)
        if (
            value < ModelLimits.MIN_PRESENCE_PENALTY
            or value > ModelLimits.MAX_PRESENCE_PENALTY
        ):
            raise ValueError(
                f"Presence penalty must be between {ModelLimits.MIN_PRESENCE_PENALTY} and {ModelLimits.MAX_PRESENCE_PENALTY}, it is currently: {value}"
            )
        self._presence_penalty = value
        SETTINGS_DB["presence_penalty"] = value

    @property
    def frequency_penalty(self):
        return self._frequency_penalty

    @frequency_penalty.setter
    def frequency_penalty(self, value):
        value = float(value)
        if (
            value < ModelLimits.MIN_FREQUENCY_PENALTY
            or value > ModelLimits.MAX_FREQUENCY_PENALTY
        ):
            raise ValueError(
                f"Frequency penalty must be greater between {ModelLimits.MIN_FREQUENCY_PENALTY} and {ModelLimits.MAX_FREQUENCY_PENALTY}, it is currently: {value}"
            )
        self._frequency_penalty = value
        SETTINGS_DB["frequency_penalty"] = value

    @property
    def best_of(self):
        return self._best_of

    @best_of.setter
    def best_of(self, value):
        value = int(value)
        if value < ModelLimits.MIN_BEST_OF or value > ModelLimits.MAX_BEST_OF:
            raise ValueError(
                f"Best of must be between {ModelLimits.MIN_BEST_OF} and {ModelLimits.MAX_BEST_OF}, it is currently: {value}\nNote that increasing the value of this parameter will act as a multiplier on the number of tokens requested!"
            )
        self._best_of = value
        SETTINGS_DB["best_of"] = value

    @property
    def prompt_min_length(self):
        return self._prompt_min_length

    @prompt_min_length.setter
    def prompt_min_length(self, value):
        value = int(value)
        if (
            value < ModelLimits.MIN_PROMPT_MIN_LENGTH
            or value > ModelLimits.MAX_PROMPT_MIN_LENGTH
        ):
            raise ValueError(
                f"Minimal prompt length must be between {ModelLimits.MIN_PROMPT_MIN_LENGTH} and {ModelLimits.MAX_PROMPT_MIN_LENGTH}, it is currently: {value}"
            )
        self._prompt_min_length = value
        SETTINGS_DB["prompt_min_length"] = value

    @staticmethod
    def backoff_handler_http(details):
        print(
            f"Retrying OpenAI request after {details['wait']:0.1f}s ({details['tries']} attempts)"
        )

    def _request_headers(self, custom_api_key=None):
        headers = {"Authorization": f"Bearer {custom_api_key or self.openai_key}"}
        # A user's key can belong to a different organization/project.
        if not custom_api_key:
            if self.openai_organization:
                headers["OpenAI-Organization"] = self.openai_organization
            if self.openai_project:
                headers["OpenAI-Project"] = self.openai_project
        return headers

    @backoff.on_exception(
        backoff.expo,
        (OpenAIAPIError, aiohttp.ClientConnectionError, asyncio.TimeoutError),
        max_tries=4,
        giveup=lambda error: isinstance(error, OpenAIAPIError) and not error.retryable,
    )
    async def _post_json(self, endpoint, payload, custom_api_key=None):
        url = f"{self.openai_base_url}/{endpoint.lstrip('/')}"
        async with aiohttp.ClientSession(
            raise_for_status=False,
            timeout=aiohttp.ClientTimeout(total=300),
        ) as session:
            async with session.post(
                url,
                json=payload,
                proxy=EnvService.get_http_proxy(url),
                headers=self._request_headers(custom_api_key),
            ) as response:
                try:
                    result = await response.json()
                except (ValueError, aiohttp.ContentTypeError) as error:
                    raise OpenAIAPIError(
                        "The API returned a non-JSON response", response.status
                    ) from error
                if not isinstance(result, dict):
                    raise OpenAIAPIError(
                        "The API returned an invalid JSON response", response.status
                    )
                if response.status >= 400 or "error" in result:
                    error = result.get("error", {})
                    message = (
                        error.get("message", "OpenAI request failed")
                        if isinstance(error, dict)
                        else str(error)
                    )
                    raise OpenAIAPIError(
                        message,
                        response.status,
                        error.get("code") if isinstance(error, dict) else None,
                    )
                return result

    def _chat_payload(
        self,
        model,
        messages,
        *,
        temp_override=None,
        top_p_override=None,
        frequency_penalty_override=None,
        presence_penalty_override=None,
        max_tokens_override=None,
        stop=None,
        respond_json=False,
        input_tokens=None,
    ):
        budget = (
            self.max_tokens if max_tokens_override is None else int(max_tokens_override)
        )
        if budget < 1:
            raise ValueError("The output token budget must be positive")
        budget = min(budget, Models.get_max_output_tokens(model))
        if input_tokens is not None:
            remaining = Models.get_max_tokens(model) - int(input_tokens)
            if remaining < 1:
                raise ValueError("The prompt exceeds the model's context window")
            budget = min(budget, remaining)
        # Developer messages are the current instruction role for reasoning models.
        messages = [dict(message) for message in messages]
        if Models.is_reasoning(model):
            for message in messages:
                if message["role"] == "system":
                    message["role"] = "developer"
        payload = {
            "model": model,
            "messages": messages,
            Models.token_parameter(model): budget,
        }
        if not Models.is_reasoning(model):
            payload.update(
                {
                    "temperature": (
                        self.temp if temp_override is None else temp_override
                    ),
                    "top_p": self.top_p if top_p_override is None else top_p_override,
                    "presence_penalty": (
                        self.presence_penalty
                        if presence_penalty_override is None
                        else presence_penalty_override
                    ),
                    "frequency_penalty": (
                        self.frequency_penalty
                        if frequency_penalty_override is None
                        else frequency_penalty_override
                    ),
                }
            )
            if stop:
                payload["stop"] = stop
        if respond_json:
            payload["response_format"] = {"type": "json_object"}
        return payload

    async def valid_text_request(self, response, model=None):
        if "error" in response:
            raise ValueError(
                str(response["error"].get("message", "The API returned an error"))
            )
        if "usage" not in response or not response.get("choices"):
            raise ValueError("The API returned an invalid chat completion")
        await self.usage_service.update_usage_from_response(
            response["usage"], model or self.model
        )

    async def _send_chat(self, payload, custom_api_key=None):
        response = await self._post_json("chat/completions", payload, custom_api_key)
        await self.valid_text_request(response, payload["model"])
        choice = response["choices"][0]
        message = choice.get("message", {})
        if not message.get("content"):
            if message.get("refusal"):
                message["content"] = message["refusal"]
            elif choice.get("finish_reason") == "length":
                raise ValueError(
                    "The model exhausted its output budget before producing text. Increase max_tokens, especially for reasoning models."
                )
            else:
                raise ValueError("The API returned no response text")
        return response

    async def send_embedding_request(self, text, custom_api_key=None):
        model = EnvService.get_openai_embedding_model() or Models.EMBEDDINGS
        payload = {"model": model, "input": text}
        if model.startswith("text-embedding-3-"):
            payload["dimensions"] = (
                1536  # Existing Pinecone memory indexes use 1536 dimensions.
            )
        response = await self._post_json("embeddings", payload, custom_api_key)
        if not response.get("data") or "embedding" not in response["data"][0]:
            raise ValueError("The API returned an invalid embedding")
        if "usage" in response:
            await self.usage_service.update_usage_from_response(
                response["usage"], model
            )
        return response["data"][0]["embedding"]

    async def send_edit_request(
        self,
        instruction,
        text=None,
        temp_override=None,
        top_p_override=None,
        custom_api_key=None,
    ):
        return await self._send_chat(
            self._chat_payload(
                Models.LOW_USAGE_MODEL,
                [
                    {
                        "role": "system",
                        "content": "Edit the provided text according to the instruction. Return only the edited result.",
                    },
                    {
                        "role": "user",
                        "content": f"Instruction: {instruction}\n\nText to edit: {text or ''}",
                    },
                ],
                temp_override=temp_override,
                top_p_override=top_p_override,
            ),
            custom_api_key,
        )

    async def send_moderations_request(self, text):
        return await self._post_json(
            "moderations",
            {
                "model": EnvService.get_openai_moderation_model() or Models.MODERATION,
                "input": text,
            },
        )

    async def send_summary_request(self, prompt, custom_api_key=None):
        return await self._send_chat(
            self._chat_payload(
                self.model,
                [
                    {
                        "role": "system",
                        "content": "Summarize the conversation in detail. Preserve names, user preferences, relevant personal details, and unresolved requests. Do not summarize the assistant's instructions.",
                    },
                    {"role": "user", "content": prompt},
                ],
            ),
            custom_api_key,
        )

    async def send_language_detect_request(self, text, pretext, custom_api_key=None):
        return await self._send_chat(
            self._chat_payload(
                Models.LOW_USAGE_MODEL,
                [
                    {"role": "system", "content": pretext},
                    {"role": "user", "content": text},
                ],
                temp_override=0,
            ),
            custom_api_key,
        )

    def cleanse_username(self, text):
        # Chat Completions message names accept only these characters, up to 64.
        return re.sub(r"[^a-zA-Z0-9_-]", "_", text.strip())[:64] or "user"

    @staticmethod
    def _message_content(text, image_urls, model):
        if not image_urls or not Models.supports_vision(model):
            return text
        return [{"type": "text", "text": text}] + [
            {"type": "image_url", "image_url": {"url": url, "detail": "auto"}}
            for url in image_urls
        ]

    async def send_chatgpt_chat_request(
        self,
        prompt_history,
        model,
        bot_name,
        user_displayname,
        temp_override=None,
        top_p_override=None,
        best_of_override=None,
        frequency_penalty_override=None,
        presence_penalty_override=None,
        max_tokens_override=None,
        stop=None,
        custom_api_key=None,
        system_prompt_override=None,
        respond_json=None,
    ):
        model_selection = model or self.model
        messages = []
        if system_prompt_override:
            messages.append({"role": "system", "content": system_prompt_override})
        if respond_json:
            messages.append(
                {"role": "system", "content": "Return a valid JSON object."}
            )
        for number, message in enumerate(prompt_history):
            text = message.text.replace("<|endofstatement|>", "")
            if number == 0 and not system_prompt_override:
                messages.append({"role": "system", "content": text})
                continue
            if (
                text.strip()
                .lower()
                .startswith("this conversation has some context from earlier")
            ):
                messages.append({"role": "system", "content": text})
                continue
            role, username = "user", user_displayname
            if text.startswith(f"\n{bot_name}"):
                role, username = "assistant", bot_name
                text = text.removeprefix(f"\n{bot_name}").lstrip(": ")
            else:
                match = re.match(r"\s*([^\n:]+):", text)
                if match:
                    username = match[1]
                    text = text[match.end() :].rstrip()
            messages.append(
                {
                    "role": role,
                    "name": self.cleanse_username(username),
                    "content": self._message_content(
                        text, getattr(message, "image_urls", None), model_selection
                    ),
                }
            )
        response = await self._send_chat(
            self._chat_payload(
                model_selection,
                messages,
                temp_override=temp_override,
                top_p_override=top_p_override,
                frequency_penalty_override=frequency_penalty_override,
                presence_penalty_override=presence_penalty_override,
                max_tokens_override=max_tokens_override,
                stop=stop,
                respond_json=bool(respond_json),
            ),
            custom_api_key,
        )
        if respond_json:
            content = response["choices"][0]["message"].get("content")
            try:
                result = json.loads(content)
            except (TypeError, json.JSONDecodeError) as error:
                raise ValueError(
                    "Could not decode JSON response from the API"
                ) from error
            if not isinstance(result, dict):
                raise ValueError("The API did not return a JSON object")
            return result
        return response

    async def send_transcription_request(
        self, file, temperature_override=None, custom_api_key=None
    ):
        data = aiohttp.FormData()
        data.add_field(
            "model", os.getenv("OPENAI_TRANSCRIPTION_MODEL", "gpt-4o-mini-transcribe")
        )
        if isinstance(file, discord.Attachment):
            content = await read_attachment_bytes(file)
            content_type = file.content_type or "application/octet-stream"
        else:
            content = await asyncio.to_thread(file.fp.read)
            content_type = "application/octet-stream"
        data.add_field(
            "file",
            content,
            filename=file.filename or "audio.mp4",
            content_type=content_type,
        )
        if temperature_override is not None:
            data.add_field("temperature", str(temperature_override))
        url = f"{self.openai_base_url}/audio/transcriptions"
        async with aiohttp.ClientSession(
            raise_for_status=False,
            timeout=aiohttp.ClientTimeout(total=300),
        ) as session:
            async with session.post(
                url,
                proxy=EnvService.get_http_proxy(url),
                headers=self._request_headers(custom_api_key),
                data=data,
            ) as resp:
                response = await resp.json()
                if resp.status >= 400 or "error" in response:
                    raise OpenAIAPIError(
                        response.get("error", {}).get(
                            "message", "Transcription failed"
                        ),
                        resp.status,
                    )
                return response["text"]

    async def send_request(
        self,
        prompt,
        tokens,
        temp_override=None,
        top_p_override=None,
        best_of_override=None,
        frequency_penalty_override=None,
        presence_penalty_override=None,
        max_tokens_override=None,
        model=None,
        stop=None,
        custom_api_key=None,
        is_chatgpt_request=False,
        system_instruction=None,
    ):
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})
        return await self._send_chat(
            self._chat_payload(
                model or self.model,
                messages,
                temp_override=temp_override,
                top_p_override=top_p_override,
                frequency_penalty_override=frequency_penalty_override,
                presence_penalty_override=presence_penalty_override,
                max_tokens_override=max_tokens_override,
                stop=stop,
                input_tokens=tokens,
            ),
            custom_api_key,
        )

    @staticmethod
    async def send_test_request(api_key):
        # Called only after the user explicitly submits a key for validation.
        selected_model = EnvService.get_openai_model() or Models.LOW_USAGE_MODEL
        url = f"{EnvService.get_openai_base_url().rstrip('/')}/chat/completions"
        async with aiohttp.ClientSession(
            raise_for_status=False,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as session:
            async with session.post(
                url,
                proxy=EnvService.get_http_proxy(url),
                json={
                    "model": selected_model,
                    "messages": [{"role": "user", "content": "Reply OK."}],
                    Models.token_parameter(selected_model): 128,
                },
                headers={"Authorization": f"Bearer {api_key}"},
            ) as resp:
                response = await resp.json()
                if resp.status >= 400 or "error" in response:
                    raise ValueError(
                        response.get("error", {}).get(
                            "message", "Key validation failed"
                        )
                    )
                return response

    async def save_image_urls_and_return(self, image_urls, ctx):
        """Persist source images and return a Discord-sized contact sheet."""
        if not image_urls:
            raise ValueError("The API returned no images")
        file_limit = ctx.guild.filesize_limit if ctx.guild else 10 * 1024 * 1024

        def save_images():
            os.makedirs(self.IMAGE_SAVE_PATH, exist_ok=True)
            images, paths = [], []
            for reference in image_urls:
                if reference.startswith("data:image/"):
                    try:
                        content = base64.b64decode(
                            reference.split(",", 1)[1], validate=True
                        )
                    except (ValueError, IndexError) as error:
                        raise ValueError(
                            "The API returned invalid base64 image data"
                        ) from error
                else:
                    response = requests.get(reference, timeout=60)
                    response.raise_for_status()
                    content = response.content
                with Image.open(io.BytesIO(content)) as source:
                    image = source.convert("RGBA")
                path = os.path.join(self.IMAGE_SAVE_PATH, f"{uuid.uuid4()}.png")
                image.save(path)
                paths.append(path)
                images.append(image)
            columns = math.ceil(math.sqrt(len(images)))
            rows = math.ceil(len(images) / columns)
            cell_width = max(image.width for image in images)
            cell_height = max(image.height for image in images)
            grid = Image.new("RGBA", (cell_width * columns, cell_height * rows))
            for index, image in enumerate(images):
                grid.paste(
                    image,
                    ((index % columns) * cell_width, (index // columns) * cell_height),
                )
                image.close()
            for _ in range(20):
                output = io.BytesIO()
                grid.save(output, format="PNG")
                if output.tell() <= file_limit:
                    output.seek(0)
                    grid.close()
                    return output, paths
                output.close()
                resized = grid.resize(
                    (max(1, int(grid.width * 0.8)), max(1, int(grid.height * 0.8)))
                )
                grid.close()
                grid = resized
            grid.close()
            raise ValueError(
                "Generated images could not fit this server's upload limit"
            )

        output, paths = await asyncio.to_thread(save_images)
        return discord.File(output, filename="generated-images.png"), paths

    async def _generate_image_references(
        self, prompt, quality, image_size, style, count, custom_api_key=None
    ):
        if not prompt or not prompt.strip():
            raise ValueError("An image prompt is required")
        count = int(count)
        if count < 1 or count > ModelLimits.MAX_NUM_IMAGES:
            raise ValueError(
                f"Choose between 1 and {ModelLimits.MAX_NUM_IMAGES} images"
            )
        model = EnvService.get_openai_image_model() or Models.IMAGE
        if model.startswith("dall-e"):
            raise ValueError(
                "DALL-E models have been retired. Set OPENAI_IMAGE_MODEL to a GPT Image model."
            )
        # Preserve existing slash command callers while using current Image API fields.
        quality = {"standard": "medium", "hd": "high"}.get(quality, quality) or "auto"
        image_size = {
            "256x256": "1024x1024",
            "512x512": "1024x1024",
            "1792x1024": "1536x1024",
            "1024x1792": "1024x1536",
        }.get(image_size, image_size) or "auto"
        if quality not in ("auto", "low", "medium", "high", "xhigh", "max"):
            raise ValueError("Unknown image quality")
        if image_size not in ("auto", "1024x1024", "1536x1024", "1024x1536"):
            raise ValueError("Unknown image size")
        if style in ("vivid", "natural"):
            prompt = f"{prompt}\n\nUse a {style} visual style."
        response = await self._post_json(
            "images/generations",
            {
                "model": model,
                "prompt": prompt,
                "n": count,
                "size": image_size,
                "quality": quality,
                "output_format": "png",
            },
            custom_api_key,
        )
        references = []
        for result in response.get("data", []):
            if result.get("b64_json"):
                references.append(f"data:image/png;base64,{result['b64_json']}")
            elif result.get("url"):
                # Also accept compatible providers returning signed download URLs.
                references.append(result["url"])
        if not references:
            raise ValueError("The API returned no image data")
        if "usage" in response:
            await self.usage_service.update_usage_from_response(
                response["usage"], model
            )
        return references

    async def send_image_request(
        self, ctx, prompt, quality, image_size, style, custom_api_key=None
    ):
        references = await self._generate_image_references(
            prompt, quality, image_size, style, self.num_images, custom_api_key
        )
        return await self.save_image_urls_and_return(references, ctx)

    async def send_image_request_within_conversation(
        self, prompt, quality, image_size, style, custom_api_key=None, num_images=1
    ):
        return await self._generate_image_references(
            prompt, quality, image_size, style, num_images, custom_api_key
        )

    async def send_image_request_old(self, ctx, prompt, vary=None, custom_api_key=None):
        if vary:
            raise ValueError(
                "DALL-E image variations have been retired. Generate a new image with /draw instead."
            )
        return await self.send_image_request(
            ctx, prompt, "auto", self.image_size, None, custom_api_key
        )
