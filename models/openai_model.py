import asyncio
import functools
import math
import os
import re
import tempfile
import traceback
import uuid
from typing import Any, Tuple

import aiohttp
import backoff
import discord

# An enum of two modes, TOP_P or TEMPERATURE
import requests
from services.environment_service import EnvService
from PIL import Image
from discord import File
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
    # Text models
    DAVINCI = "text-davinci-003"
    CURIE = "text-curie-001"

    # Embedding models
    EMBEDDINGS = "text-embedding-ada-002"

    # Edit models
    EDIT = "text-davinci-edit-001"

    # ChatGPT Models
    TURBO = "gpt-3.5-turbo"
    TURBO_16 = "gpt-3.5-turbo-16k"
    TURBO_DEV = "gpt-3.5-turbo-0613"
    TURBO_16_DEV = "gpt-3.5-turbo-16k-0613"

    # GPT4 Models
    GPT4 = "gpt-4"
    GPT4_32 = "gpt-4-32k"
    GPT4_DEV = "gpt-4-0613"
    GPT4_32_DEV = "gpt-4-32k-0613"

    # Model collections
    TEXT_MODELS = [
        DAVINCI,
        CURIE,
        TURBO,
        TURBO_16,
        TURBO_DEV,
        TURBO_16_DEV,
        GPT4,
        GPT4_32,
        GPT4_DEV,
        GPT4_32_DEV,
    ]
    CHATGPT_MODELS = [
        TURBO,
        TURBO_16,
        TURBO_DEV,
        TURBO_16_DEV,
    ]
    GPT4_MODELS = [
        GPT4,
        GPT4_32,
        GPT4_DEV,
        GPT4_32_DEV,
    ]
    EDIT_MODELS = [EDIT]

    DEFAULT = TURBO
    LOW_USAGE_MODEL = CURIE

    # Tokens Mapping
    TOKEN_MAPPING = {
        DAVINCI: 4024,
        CURIE: 2024,
        TURBO: 4096,
        TURBO_16: 16384,
        TURBO_DEV: 4096,
        TURBO_16_DEV: 16384,
        GPT4: 8192,
        GPT4_32: 32768,
        GPT4_DEV: 8192,
        GPT4_32_DEV: 32768,
    }

    @staticmethod
    def get_max_tokens(model: str) -> int:
        return Models.TOKEN_MAPPING.get(model, 2024)


class ImageSize:
    SMALL = "256x256"
    MEDIUM = "512x512"
    LARGE = "1024x1024"

    ALL_SIZES = [SMALL, MEDIUM, LARGE]


class ModelLimits:
    MIN_TOKENS = 15
    MAX_TOKENS = 32000

    MIN_CONVERSATION_LENGTH = 1
    MAX_CONVERSATION_LENGTH = 100000

    MIN_SUMMARIZE_THRESHOLD = 1500
    MAX_SUMMARIZE_THRESHOLD = 30000

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
        self.model = (
            SETTINGS_DB["model"]
            if "model" in SETTINGS_DB and SETTINGS_DB["model"] in Models.TEXT_MODELS
            else Models.DEFAULT
        )
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
        ]

        self.openai_key = EnvService.get_openai_token()
        self.openai_organization = EnvService.get_openai_organization()

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
        # convert value string into boolean
        if value.lower() == "true":
            value = True
        elif value.lower() == "false":
            value = False
        else:
            raise ValueError("Value must be either `true` or `false`!")

        if value:
            self._model = Models.LOW_USAGE_MODEL
            self.max_tokens = 1900
            self.model_max_tokens = 1000
        else:
            self._model = Models.DEFAULT
            self.max_tokens = 4000
            self.model_max_tokens = 4024

    @property
    def model(self):
        return self._model

    @model.setter
    def model(self, model):
        if model not in Models.TEXT_MODELS:
            raise ValueError(f"Invalid model, must be one of: {Models.TEXT_MODELS}")
        self._model = model

        # Set the token count
        self._max_tokens = Models.get_max_tokens(self._model)
        SETTINGS_DB["model"] = model

        # Set the summarize threshold if the model was set to gpt-4
        if "gpt-4" in self._model:
            self._summarize_threshold = 28000
        elif "gpt-3" in self._model:
            self._summarize_threshold = 6000

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

    def backoff_handler_http(details):
        print(
            f"Backing off {details['wait']:0.1f} seconds after {details['tries']} tries calling function {details['target']} | "
            f"{details['exception'].status}: {details['exception'].message}"
        )

    def backoff_handler_request(details):
        print(
            f"Backing off {details['wait']:0.1f} seconds after {details['tries']} tries calling function {details['target']} | "
            f"{details['exception'].args[0]}"
        )

    async def valid_text_request(self, response, model=None):
        try:
            tokens_used = int(response["usage"]["total_tokens"])
            if model and model in Models.EDIT_MODELS:
                pass
            else:
                await self.usage_service.update_usage(
                    tokens_used, await self.usage_service.get_cost_name(model)
                )
        except Exception as e:
            traceback.print_exc()
            if "error" in response:
                raise ValueError(
                    "The API returned an invalid response: "
                    + str(response["error"]["message"])
                ) from e
            else:
                raise ValueError("The API returned an invalid response") from e

    @backoff.on_exception(
        backoff.expo,
        aiohttp.ClientResponseError,
        factor=3,
        base=5,
        max_tries=4,
        on_backoff=backoff_handler_http,
    )
    async def send_embedding_request(self, text, custom_api_key=None):
        async with aiohttp.ClientSession(
            raise_for_status=True, timeout=aiohttp.ClientTimeout(total=300)
        ) as session:
            payload = {
                "model": Models.EMBEDDINGS,
                "input": text,
            }
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.openai_key if not custom_api_key else custom_api_key}",
            }
            self.use_org = True if "true" in str(self.use_org).lower() else False
            if self.use_org:
                if self.openai_organization:
                    headers["OpenAI-Organization"] = self.openai_organization
            async with session.post(
                "https://api.openai.com/v1/embeddings", json=payload, headers=headers
            ) as resp:
                response = await resp.json()

                try:
                    return response["data"][0]["embedding"]
                except Exception:
                    print(response)
                    traceback.print_exc()
                    return

    @backoff.on_exception(
        backoff.expo,
        ValueError,
        factor=3,
        base=5,
        max_tries=4,
        on_backoff=backoff_handler_request,
    )
    async def send_edit_request(
        self,
        instruction,
        text=None,
        temp_override=None,
        top_p_override=None,
        custom_api_key=None,
    ):
        print(
            f"The text about to be edited is [{text}] with instructions [{instruction}]"
        )
        print(f"Overrides -> temp:{temp_override}, top_p:{top_p_override}")

        async with aiohttp.ClientSession(
            raise_for_status=False, timeout=aiohttp.ClientTimeout(total=300)
        ) as session:
            payload = {
                "model": Models.EDIT,
                "input": "" if text is None else text,
                "instruction": instruction,
                "temperature": self.temp if temp_override is None else temp_override,
                "top_p": self.top_p if top_p_override is None else top_p_override,
            }
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.openai_key if not custom_api_key else custom_api_key}",
            }
            self.use_org = True if "true" in str(self.use_org).lower() else False
            if self.use_org:
                if self.openai_organization:
                    headers["OpenAI-Organization"] = self.openai_organization
            async with session.post(
                "https://api.openai.com/v1/edits", json=payload, headers=headers
            ) as resp:
                response = await resp.json()
                await self.valid_text_request(response, model=Models.EDIT)
                return response

    @backoff.on_exception(
        backoff.expo,
        aiohttp.ClientResponseError,
        factor=3,
        base=5,
        max_tries=6,
        on_backoff=backoff_handler_http,
    )
    async def send_moderations_request(self, text):
        # Use aiohttp to send the above request:
        async with aiohttp.ClientSession(raise_for_status=True) as session:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.openai_key}",
            }
            payload = {"input": text}
            async with session.post(
                "https://api.openai.com/v1/moderations",
                headers=headers,
                json=payload,
            ) as response:
                return await response.json()

    @backoff.on_exception(
        backoff.expo,
        ValueError,
        factor=3,
        base=5,
        max_tries=4,
        on_backoff=backoff_handler_request,
    )
    async def send_summary_request(self, prompt, custom_api_key=None):
        """
        Sends a summary request to the OpenAI API
        """
        summary_request_text = []
        summary_request_text.append(
            "The following is a conversation instruction set and a conversation between two people, a <username>, and GPTie."
            " Firstly, determine the <username>'s name from the conversation history, then summarize the conversation."
            " Do not summarize the instructions for GPTie, only the conversation. Summarize the conversation in a detailed fashion. If <username> mentioned"
            " their name, be sure to mention it in the summary. Pay close attention to things the <username> has told you, such as personal details."
        )
        summary_request_text.append(prompt + "\nDetailed summary of conversation: \n")

        summary_request_text = "".join(summary_request_text)

        messages = []
        messages.append(
            {
                "role": "system",
                "content": summary_request_text,
            }
        )

        async with aiohttp.ClientSession(
            raise_for_status=False, timeout=aiohttp.ClientTimeout(total=300)
        ) as session:
            payload = {
                "model": self.model if self.model is not None else Models.GPT4_32,
                "messages": messages,
                "temperature": self.temp,
                "top_p": self.top_p,
                "presence_penalty": self.presence_penalty,
                "frequency_penalty": self.frequency_penalty,
            }
            headers = {
                "Authorization": f"Bearer {self.openai_key if not custom_api_key else custom_api_key}"
            }
            self.use_org = True if "true" in str(self.use_org).lower() else False
            if self.use_org:
                if self.openai_organization:
                    headers["OpenAI-Organization"] = self.openai_organization

            async with session.post(
                "https://api.openai.com/v1/chat/completions",
                json=payload,
                headers=headers,
            ) as resp:
                response = await resp.json()
                # print(f"Payload -> {payload}")
                # Parse the total tokens used for this request and response pair from the response
                await self.valid_text_request(
                    response, model=self.model if self.model is not None else Models.GPT4_32
                )
                print(f"Summary response -> {response}")

                return response

    @backoff.on_exception(
        backoff.expo,
        ValueError,
        factor=3,
        base=5,
        max_tries=4,
        on_backoff=backoff_handler_request,
    )
    async def send_language_detect_request(
        self,
        text,
        pretext,
    ) -> (
        Tuple[dict, bool]
    ):  # The response, and a boolean indicating whether or not the context limit was reached.
        # Validate that  all the parameters are in a good state before we send the request

        prompt = f"{pretext}{text}\nOutput:"

        max_tokens = Models.get_max_tokens(
            Models.DAVINCI
        ) - self.usage_service.count_tokens(prompt)

        print(f"Language detection request for {text}")

        async with aiohttp.ClientSession(raise_for_status=False) as session:
            payload = {
                "model": Models.DAVINCI,
                "prompt": prompt,
                "temperature": 0,
                "top_p": 1,
                "max_tokens": max_tokens,
            }
            headers = {"Authorization": f"Bearer {self.openai_key}"}
            self.use_org = True if "true" in str(self.use_org).lower() else False
            if self.use_org:
                if self.openai_organization:
                    headers["OpenAI-Organization"] = self.openai_organization
            async with session.post(
                "https://api.openai.com/v1/completions", json=payload, headers=headers
            ) as resp:
                response = await resp.json()

                await self.valid_text_request(response)
                print(f"Response -> {response}")

                return response

    def cleanse_username(self, text):
        text = text.strip()
        text = text.replace(":", "")
        text = text.replace(" ", "")
        # Replace any character that's not a letter or number with an underscore
        text = re.sub(r"[^a-zA-Z0-9]", "_", text)
        return text

    @backoff.on_exception(
        backoff.expo,
        ValueError,
        factor=3,
        base=5,
        max_tries=4,
        on_backoff=backoff_handler_request,
    )
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
    ) -> (
        Tuple[dict, bool]
    ):  # The response, and a boolean indicating whether or not the context limit was reached.
        # Validate that  all the parameters are in a good state before we send the request

        # Clean up the bot name
        bot_name_clean = self.cleanse_username(bot_name)

        # Format the request body into the messages format that the API is expecting
        #   "messages": [{"role": "user", "content": "Hello!"}]
        messages = []
        for number, message in enumerate(prompt_history):
            if number == 0:
                # If this is the first message, it is the context prompt.
                messages.append(
                    {
                        "role": "user",
                        "content": message.text,
                    }
                )
                continue

            if message.text.startswith(f"\n{bot_name}"):
                text = message.text.replace(bot_name, "")
                text = text.replace("<|endofstatement|>", "")
                messages.append(
                    {
                        "role": "assistant",
                        "content": text,
                    }  # TODO add back the assistant's name when the API is fixed..
                )
            else:
                try:
                    if (
                        message.text.strip()
                        .lower()
                        .startswith("this conversation has some context from earlier")
                    ):
                        raise Exception("This is a context message")

                    username = re.search(r"(?<=\n)(.*?)(?=:)", message.text).group()
                    username_clean = self.cleanse_username(username)
                    text = message.text.replace(f"{username}:", "")
                    # Strip whitespace just from the right side of the string
                    text = text.rstrip()
                    text = text.replace("<|endofstatement|>", "")
                    messages.append(
                        {"role": "user", "name": username_clean, "content": text}
                    )
                except Exception:
                    text = message.text.replace("<|endofstatement|>", "")
                    messages.append({"role": "system", "content": text})

        print(f"Messages -> {messages}")
        async with aiohttp.ClientSession(
            raise_for_status=False, timeout=aiohttp.ClientTimeout(total=300)
        ) as session:
            payload = {
                "model": self.model if not model else model,
                "messages": messages,
                "stop": "" if stop is None else stop,
                "temperature": self.temp if temp_override is None else temp_override,
                "top_p": self.top_p if top_p_override is None else top_p_override,
                "presence_penalty": self.presence_penalty
                if presence_penalty_override is None
                else presence_penalty_override,
                "frequency_penalty": self.frequency_penalty
                if frequency_penalty_override is None
                else frequency_penalty_override,
            }
            headers = {
                "Authorization": f"Bearer {self.openai_key if not custom_api_key else custom_api_key}"
            }
            self.use_org = True if "true" in str(self.use_org).lower() else False
            if self.use_org:
                if self.openai_organization:
                    headers["OpenAI-Organization"] = self.openai_organization

            async with session.post(
                "https://api.openai.com/v1/chat/completions",
                json=payload,
                headers=headers,
            ) as resp:
                response = await resp.json()
                # print(f"Payload -> {payload}")
                # Parse the total tokens used for this request and response pair from the response
                await self.valid_text_request(
                    response, model=self.model if model is None else model
                )
                print(f"Response -> {response}")

                return response

    @backoff.on_exception(
        backoff.expo,
        ValueError,
        factor=3,
        base=5,
        max_tries=4,
        on_backoff=backoff_handler_request,
    )
    async def send_transcription_request(
        self,
        file: [discord.Attachment, discord.File],
        temperature_override=None,
        custom_api_key=None,
    ):
        async with aiohttp.ClientSession(raise_for_status=True) as session:
            data = aiohttp.FormData()
            data.add_field("model", "whisper-1")
            print("audio." + file.filename.split(".")[-1])
            # TODO: make async
            data.add_field(
                "file",
                file.read() if isinstance(file, discord.Attachment) else file.fp.read(),
                filename="audio." + file.filename.split(".")[-1]
                if isinstance(file, discord.Attachment)
                else "audio.mp4",
                content_type=file.content_type
                if isinstance(file, discord.Attachment)
                else "video/mp4",
            )

            if temperature_override:
                data.add_field("temperature", temperature_override)

            async with session.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={
                    "Authorization": f"Bearer {self.openai_key if not custom_api_key else custom_api_key}",
                },
                data=data,
            ) as resp:
                response = await resp.json()
                return response["text"]

    @backoff.on_exception(
        backoff.expo,
        ValueError,
        factor=3,
        base=5,
        max_tries=4,
        on_backoff=backoff_handler_request,
    )
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
    ):  # The response, and a boolean indicating whether or not the context limit was reached.
        # Validate that  all the parameters are in a good state before we send the request

        if not max_tokens_override:
            if (
                model
                and model not in Models.GPT4_MODELS
                and model not in Models.CHATGPT_MODELS
            ):
                max_tokens_override = Models.get_max_tokens(model) - tokens

        messages = [{"role": "user", "content": prompt}]
        # modify prompt if a system instruction is set
        if system_instruction and is_chatgpt_request:
            messages = [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt},
            ]
        elif system_instruction:
            prompt = f"{system_instruction} {prompt}"

        if system_instruction:
            print(f"The instruction added to the prompt will be {system_instruction}")
        print(f"The prompt about to be sent is {prompt}")
        print(
            f"Overrides -> temp:{temp_override}, top_p:{top_p_override} frequency:{frequency_penalty_override}, presence:{presence_penalty_override}, model:{model if model else 'none'}, stop:{stop}"
        )

        # Non-ChatGPT simple completion models.
        if not is_chatgpt_request:
            async with aiohttp.ClientSession(
                raise_for_status=False, timeout=aiohttp.ClientTimeout(total=300)
            ) as session:
                payload = {
                    "model": self.model if model is None else model,
                    "prompt": prompt,
                    "stop": "" if stop is None else stop,
                    "temperature": self.temp
                    if temp_override is None
                    else temp_override,
                    "top_p": self.top_p if top_p_override is None else top_p_override,
                    "max_tokens": self.max_tokens - tokens
                    if max_tokens_override is None
                    else max_tokens_override,
                    "presence_penalty": self.presence_penalty
                    if presence_penalty_override is None
                    else presence_penalty_override,
                    "frequency_penalty": self.frequency_penalty
                    if frequency_penalty_override is None
                    else frequency_penalty_override,
                    "best_of": self.best_of
                    if not best_of_override
                    else best_of_override,
                }
                headers = {
                    "Authorization": f"Bearer {self.openai_key if not custom_api_key else custom_api_key}"
                }
                self.use_org = True if "true" in str(self.use_org).lower() else False
                if self.use_org:
                    if self.openai_organization:
                        headers["OpenAI-Organization"] = self.openai_organization

                async with session.post(
                    "https://api.openai.com/v1/completions",
                    json=payload,
                    headers=headers,
                ) as resp:
                    response = await resp.json()
                    # print(f"Payload -> {payload}")
                    # Parse the total tokens used for this request and response pair from the response
                    await self.valid_text_request(
                        response, model=self.model if model is None else model
                    )
                    print(f"Response -> {response}")

                    return response
        else:  # ChatGPT/GPT4 Simple completion
            async with aiohttp.ClientSession(
                raise_for_status=False, timeout=aiohttp.ClientTimeout(total=300)
            ) as session:
                payload = {
                    "model": self.model if not model else model,
                    "messages": messages,
                    "stop": "" if stop is None else stop,
                    "temperature": self.temp
                    if temp_override is None
                    else temp_override,
                    "top_p": self.top_p if top_p_override is None else top_p_override,
                    "presence_penalty": self.presence_penalty
                    if presence_penalty_override is None
                    else presence_penalty_override,
                    "frequency_penalty": self.frequency_penalty
                    if frequency_penalty_override is None
                    else frequency_penalty_override,
                }
                headers = {
                    "Authorization": f"Bearer {self.openai_key if not custom_api_key else custom_api_key}"
                }
                self.use_org = True if "true" in str(self.use_org).lower() else False
                if self.use_org:
                    if self.openai_organization:
                        headers["OpenAI-Organization"] = self.openai_organization
                async with session.post(
                    "https://api.openai.com/v1/chat/completions",
                    json=payload,
                    headers=headers,
                ) as resp:
                    response = await resp.json()
                    # print(f"Payload -> {payload}")
                    # Parse the total tokens used for this request and response pair from the response
                    await self.valid_text_request(
                        response, model=self.model if model is None else model
                    )
                    print(f"Response -> {response}")

                    return response

    @staticmethod
    async def send_test_request(api_key):
        async with aiohttp.ClientSession() as session:
            payload = {
                "model": Models.LOW_USAGE_MODEL,
                "prompt": "test.",
                "temperature": 1,
                "top_p": 1,
                "max_tokens": 10,
            }
            headers = {"Authorization": f"Bearer {api_key}"}
            async with session.post(
                "https://api.openai.com/v1/completions", json=payload, headers=headers
            ) as resp:
                response = await resp.json()
                try:
                    int(response["usage"]["total_tokens"])
                except:
                    raise ValueError(str(response["error"]["message"]))

                return response

    @backoff.on_exception(
        backoff.expo,
        aiohttp.ClientResponseError,
        factor=3,
        base=5,
        max_tries=4,
        on_backoff=backoff_handler_http,
    )
    async def send_image_request(
        self, ctx, prompt, vary=None, custom_api_key=None
    ) -> tuple[File, list[Any]]:
        # Validate that  all the parameters are in a good state before we send the request
        words = len(prompt.split(" "))
        if words < 1 or words > 75:
            raise ValueError(
                "Prompt must be greater than 1 word and less than 75, it is currently "
                + str(words)
            )

        # print("The prompt about to be sent is " + prompt)
        await self.usage_service.update_usage_image(self.image_size)

        response = None

        if not vary:
            payload = {"prompt": prompt, "n": self.num_images, "size": self.image_size}
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.openai_key if not custom_api_key else custom_api_key}",
            }
            self.use_org = True if "true" in str(self.use_org).lower() else False
            if self.use_org:
                if self.openai_organization:
                    headers["OpenAI-Organization"] = self.openai_organization

            async with aiohttp.ClientSession(
                raise_for_status=True, timeout=aiohttp.ClientTimeout(total=300)
            ) as session:
                async with session.post(
                    "https://api.openai.com/v1/images/generations",
                    json=payload,
                    headers=headers,
                ) as resp:
                    response = await resp.json()

        else:
            async with aiohttp.ClientSession(
                raise_for_status=True, timeout=aiohttp.ClientTimeout(total=300)
            ) as session:
                data = aiohttp.FormData()
                data.add_field("n", str(self.num_images))
                data.add_field("size", self.image_size)
                with open(vary, "rb") as f:
                    data.add_field(
                        "image", f, filename="file.png", content_type="image/png"
                    )

                    async with session.post(
                        "https://api.openai.com/v1/images/variations",
                        headers={
                            "Authorization": f"Bearer {self.openai_key if not custom_api_key else custom_api_key}",
                        },
                        data=data,
                    ) as resp:
                        response = await resp.json()

        print(response)

        image_urls = []
        for result in response["data"]:
            image_urls.append(result["url"])

        # For each image url, open it as an image object using PIL
        images = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: [
                Image.open(requests.get(url, stream=True, timeout=10).raw)
                for url in image_urls
            ],
        )

        # Save all the images with a random name to self.IMAGE_SAVE_PATH
        image_names = [f"{uuid.uuid4()}.png" for _ in range(len(images))]
        for image, name in zip(images, image_names):
            await asyncio.get_running_loop().run_in_executor(
                None, image.save, f"{self.IMAGE_SAVE_PATH}/{name}"
            )

        # Update image_urls to include the local path to these new images
        image_urls = [f"{self.IMAGE_SAVE_PATH}/{name}" for name in image_names]

        widths, heights = zip(*(i.size for i in images))

        # Calculate the number of rows and columns needed for the grid
        num_rows = num_cols = int(math.ceil(math.sqrt(len(images))))

        # If there are only 2 images, set the number of rows to 1
        if len(images) == 2:
            num_rows = 1

        # Calculate the size of the combined image
        width = max(widths) * num_cols
        height = max(heights) * num_rows

        # Create a transparent image with the same size as the images
        transparent = await asyncio.get_running_loop().run_in_executor(
            None, lambda: Image.new("RGBA", (max(widths), max(heights)))
        )

        # Create a new image with the calculated size
        new_im = await asyncio.get_running_loop().run_in_executor(
            None, lambda: Image.new("RGBA", (width, height))
        )

        # Paste the images and transparent segments into the grid
        x_offset = y_offset = 0
        for im in images:
            await asyncio.get_running_loop().run_in_executor(
                None, new_im.paste, im, (x_offset, y_offset)
            )

            x_offset += im.size[0]
            if x_offset >= width:
                x_offset = 0
                y_offset += im.size[1]

        # Fill the remaining cells with transparent segments
        while y_offset < height:
            while x_offset < width:
                await asyncio.get_running_loop().run_in_executor(
                    None, new_im.paste, transparent, (x_offset, y_offset)
                )
                x_offset += transparent.size[0]

            x_offset = 0
            y_offset += transparent.size[1]

        # Save the new_im to a temporary file and return it as a discord.File
        temp_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        await asyncio.get_running_loop().run_in_executor(
            None, new_im.save, temp_file.name
        )

        # Print the filesize of new_im, in mega bytes
        image_size = os.path.getsize(temp_file.name) / 1048576
        if ctx.guild is None:
            guild_file_limit = 8
        else:
            guild_file_limit = ctx.guild.filesize_limit / 1048576

        # If the image size is greater than 8MB, we can't return this to the user, so we will need to downscale the
        # image and try again
        safety_counter = 0
        while image_size > guild_file_limit:
            safety_counter += 1
            if safety_counter >= 3:
                break
            print(
                f"Image size is {image_size}MB, which is too large for this server {guild_file_limit}MB. Downscaling and trying again"
            )
            # We want to do this resizing asynchronously, so that it doesn't block the main thread during the resize.
            # We can use the asyncio.run_in_executor method to do this
            new_im = await asyncio.get_running_loop().run_in_executor(
                None,
                functools.partial(
                    new_im.resize, (int(new_im.width / 1.05), int(new_im.height / 1.05))
                ),
            )

            temp_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            await asyncio.get_running_loop().run_in_executor(
                None, new_im.save, temp_file.name
            )
            image_size = os.path.getsize(temp_file.name) / 1000000
            print(f"New image size is {image_size}MB")

        return (discord.File(temp_file.name), image_urls)
