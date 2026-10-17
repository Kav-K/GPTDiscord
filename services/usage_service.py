import asyncio
import logging
import threading
from collections import defaultdict
from pathlib import Path

import aiofiles
from typing import Literal
import tiktoken
from requests import RequestException

_tokenizer_unavailable = False
_tokenizer_lock = threading.Lock()


def _load_tokenizer():
    global _tokenizer_unavailable
    with _tokenizer_lock:
        if _tokenizer_unavailable:
            return None
        try:
            # Successful encodings are already cached by tiktoken itself.
            return tiktoken.get_encoding("cl100k_base")
        except (RequestException, OSError):
            _tokenizer_unavailable = True
            logging.getLogger(__name__).warning(
                "Tokenizer vocabulary unavailable; using conservative UTF-8 byte counts "
                "for local token estimates until the bot restarts. API usage accounting is unchanged."
            )
            return None


class UsageService:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.usage_file_path = data_dir / "usage.txt"
        self._usage_lock = asyncio.Lock()
        # If the usage.txt file doesn't currently exist in the directory, create it and write 0.00 to it.
        if not self.usage_file_path.exists():
            with self.usage_file_path.open("w") as f:
                f.write("0.00")
                f.close()
        self._tokenizer = None
        self.usage = defaultdict()

    @property
    def tokenizer(self):
        # tiktoken downloads its vocabulary on first use; importing the bot and
        # assembling its cogs must not require network access.
        if self._tokenizer is None:
            self._tokenizer = _load_tokenizer()
        return self._tokenizer

    COST_MAPPING = {
        "gpt4": 0.06,
        "gpt4-32": 0.12,
        "turbo": 0.002,
        "turbo-16": 0.002,
        "davinci": 0.02,
        "curie": 0.002,
        "embedding": 0.0001,
        "gpt-turbo": 0.03,
    }

    MODEL_COST_MAP = {
        "gpt-4": "gpt4",
        "gpt-4-32k": "gpt4-32",
        "gpt-4-0613": "gpt4",
        "gpt-4-32k-0613": "gpt4-32",
        "gpt-3.5-turbo": "turbo",
        "gpt-3.5-turbo-16k": "turbo-16",
        "gpt-3.5-turbo-0613": "turbo",
        "gpt-3.5-turbo-16k-0613": "turbo",
        "text-davinci-003": "davinci",
        "text-curie-001": "curie",
        "gpt-4-1106-preview": "gpt-turbo",
        "gpt-4-1106-vision-preview": "gpt-turbo",
    }

    ModeType = Literal["gpt4", "gpt4-32k", "turbo", "turbo-16k", "davinci", "embedding"]

    @staticmethod
    async def get_model_cost(mode: ModeType) -> float:
        if mode in UsageService.TOKEN_RATES:
            rates = UsageService.TOKEN_RATES[mode]
            return rates[0 if mode.startswith("text-embedding-") else 1] / 1000
        return UsageService.COST_MAPPING.get(mode, 0)

    @staticmethod
    async def get_cost_name(model) -> str:
        return UsageService.MODEL_COST_MAP.get(model, model)

    async def get_price(self, tokens_used, mode: ModeType = None):
        if isinstance(tokens_used, str) or isinstance(tokens_used, int):
            tokens_used = int(tokens_used)
        else:
            tokens_used = int(len(tokens_used))
        price = (tokens_used / 1000) * await self.get_model_cost(
            mode
        )  # This is a very rough estimate
        price = round(price, 6)
        return price

    # USD per million tokens: input, output, cached input. Verified against
    # https://developers.openai.com/api/docs/models (October 2026).
    # Unknown/custom models are explicitly excluded instead of using Davinci prices.
    TOKEN_RATES = {
        "gpt-6.1-sol": (2.0, 10.0, 0.10),
        "gpt-6-astra": (10.0, 50.0, 1.0),
        "gpt-6-sol": (2.0, 10.0, 0.20),
        "gpt-6-luna": (0.10, 0.50, 0.01),
        "gpt-5.5": (5.0, 30.0, 0.50),
        "gpt-5.4": (2.50, 15.0, 0.25),
        "gpt-5.4-mini": (0.75, 4.50, 0.075),
        "gpt-5.4-nano": (0.20, 1.25, 0.02),
        "gpt-5.2": (1.75, 14.0, 0.175),
        "gpt-5.1": (1.25, 10.0, 0.125),
        "gpt-5": (1.25, 10.0, 0.125),
        "gpt-5-mini": (0.25, 2.0, 0.025),
        "gpt-5-nano": (0.05, 0.40, 0.005),
        "o3": (1.0, 4.0, 0.25),
        "gpt-4.1": (2.0, 8.0, 0.50),
        "gpt-4.1-mini": (0.40, 1.60, 0.10),
        "gpt-4.1-nano": (0.10, 0.40, 0.025),
        "gpt-4o": (2.50, 10.0, 1.25),
        "gpt-4o-mini": (0.15, 0.60, 0.075),
        "text-embedding-3-small": (0.02, 0.0, 0.02),
        "text-embedding-3-large": (0.13, 0.0, 0.13),
        "text-embedding-ada-002": (0.10, 0.0, 0.10),
    }

    @classmethod
    def token_rates(cls, model):
        # Accept dated snapshots of known aliases; do not guess arbitrary families.
        for name in sorted(cls.TOKEN_RATES, key=len, reverse=True):
            if model == name or (model and model.startswith(name + "-20")):
                return cls.TOKEN_RATES[name]
        return None

    async def _add_cost(self, price):
        async with self._usage_lock:
            usage = await self.get_usage()
            async with aiofiles.open(self.usage_file_path, "w") as f:
                await f.write(str(round(usage + price, 8)))
        return price

    async def update_usage_from_response(self, usage, model):
        """Estimate text/embedding cost from actual input/output token counts.

        Accept Chat Completions or Responses usage dictionaries. These local
        estimates exclude image/audio/search-tool fees and are not billing data.
        """
        rates = self.token_rates(model)
        if not rates:
            logging.getLogger(__name__).warning(
                "No price configured for %s; excluded from local cost estimate", model
            )
            return None
        if hasattr(usage, "model_dump"):
            usage = usage.model_dump()
        usage = usage or {}
        input_tokens = int(
            usage.get(
                "input_tokens", usage.get("prompt_tokens", usage.get("total_tokens", 0))
            )
        )
        output_tokens = int(
            usage.get("output_tokens", usage.get("completion_tokens", 0))
        )
        details = (
            usage.get("input_tokens_details")
            or usage.get("prompt_tokens_details")
            or {}
        )
        cached_tokens = min(input_tokens, int(details.get("cached_tokens", 0)))
        input_rate, output_rate, cached_rate = rates
        # Long-context GPT-5.4/5.5 sessions have tiered pricing. Mini/nano
        # variants do not inherit this full-model threshold.
        if input_tokens > 272000 and any(
            model == name or model.startswith(name + "-20")
            for name in ("gpt-5.4", "gpt-5.5")
        ):
            input_rate *= 2
            cached_rate *= 2
            output_rate *= 1.5
        price = (
            (input_tokens - cached_tokens) * input_rate
            + cached_tokens * cached_rate
            + output_tokens * output_rate
        ) / 1_000_000
        return await self._add_cost(price)

    async def update_usage(self, tokens_used, mode=None):
        # Legacy integrations only report aggregate tokens. Their estimates use
        # the output rate conservatively when a modern model is selected.
        price = (int(tokens_used) / 1000) * await self.get_model_cost(mode)
        return await self._add_cost(price)

    async def set_usage(self, usage):
        async with self._usage_lock:
            async with aiofiles.open(self.usage_file_path, "w") as f:
                await f.write(str(usage))

    async def get_usage(self):
        async with aiofiles.open(self.usage_file_path, "r") as f:
            usage = float((await f.read()).strip())
            await f.close()
        return usage

    def count_tokens(self, text):
        tokenizer = self.tokenizer
        if tokenizer is None:
            # Byte-level BPE tokens cannot outnumber the original UTF-8 bytes.
            return len(text.encode("utf-8"))
        res = tokenizer.encode(text)
        return len(res)

    async def update_usage_image(self, image_size):
        image_size = image_size.split(" ")[0]
        if image_size == "1024x1024":
            price = 0.04
        elif image_size == "512x512":
            price = 0.018
        elif image_size == "256x256":
            price = 0.016
        elif image_size == "1792x1024":
            price = 0.08
        elif image_size == "1024x1792":
            price = 0.08
        else:
            raise ValueError("Invalid image size")

        return await self._add_cost(price)

    def update_usage_memory(self, guild_name, functionality, usage):
        if guild_name in self.usage:
            if functionality in self.usage[guild_name]:
                self.usage[guild_name][functionality] += usage
            else:
                self.usage[guild_name][functionality] = usage
        else:
            self.usage[guild_name] = {functionality: usage}

    def get_usage_memory(self, guild_name):
        return self.usage[guild_name] if guild_name in self.usage else {}

    def get_usage_memory_all(self):
        return self.usage

    @staticmethod
    def count_tokens_static(text):
        tokenizer = _load_tokenizer()
        if tokenizer is None:
            return len(text.encode("utf-8"))
        res = tokenizer.encode(text)
        return len(res)
