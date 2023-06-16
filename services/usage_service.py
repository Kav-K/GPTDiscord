from pathlib import Path

import aiofiles
from typing import Literal
import tiktoken


class UsageService:
    def __init__(self, data_dir: Path):
        self.usage_file_path = data_dir / "usage.txt"
        # If the usage.txt file doesn't currently exist in the directory, create it and write 0.00 to it.
        if not self.usage_file_path.exists():
            with self.usage_file_path.open("w") as f:
                f.write("0.00")
                f.close()
        self.tokenizer = tiktoken.get_encoding("cl100k_base")

    COST_MAPPING = {
        "gpt4": 0.05,
        "gpt4-32": 0.1,
        "turbo": 0.0019,
        "turbo-16": 0.0038,
        "davinci": 0.02,
        "curie": 0.002,
        "embedding": 0.0001,
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
    }

    ModeType = Literal["gpt4", "gpt4-32k", "turbo", "turbo-16k", "davinci", "embedding"]

    @staticmethod
    async def get_model_cost(mode: ModeType) -> float:
        return UsageService.COST_MAPPING.get(mode, 0)

    @staticmethod
    async def get_cost_name(model) -> str:
        return UsageService.MODEL_COST_MAP.get(model, "davinci")

    async def get_price(self, tokens_used, mode: ModeType = None):
        tokens_used = int(tokens_used)
        price = (tokens_used / 1000) * await self.get_model_cost(
            mode
        )  # This is a very rough estimate
        price = round(price, 6)
        return price

    async def update_usage(
        self,
        tokens_used,
        mode: ModeType = None,
    ):
        tokens_used = int(tokens_used)
        price = (tokens_used / 1000) * await self.get_model_cost(mode)
        price = round(price, 6)
        usage = round(await self.get_usage(), 6)
        new_total = round(usage + price, 6)
        print(
            f"{'Completion' if mode != 'embedding' else 'Embed'} cost -> Old: {str(usage)} | New: {str(new_total)}, used {str(price)} credits"
        )
        async with aiofiles.open(self.usage_file_path, "w") as f:
            await f.write(str(new_total))
            await f.close()

    async def set_usage(self, usage):
        async with aiofiles.open(self.usage_file_path, "w") as f:
            await f.write(str(usage))
            await f.close()

    async def get_usage(self):
        async with aiofiles.open(self.usage_file_path, "r") as f:
            usage = float((await f.read()).strip())
            await f.close()
        return usage

    def count_tokens(self, text):
        res = self.tokenizer.encode(text)
        return len(res)

    async def update_usage_image(self, image_size):
        # 1024×1024    $0.020 / image
        # 512×512    $0.018 / image
        # 256×256    $0.016 / image

        if image_size == "1024x1024":
            price = 0.02
        elif image_size == "512x512":
            price = 0.018
        elif image_size == "256x256":
            price = 0.016
        else:
            raise ValueError("Invalid image size")

        usage = await self.get_usage()

        async with aiofiles.open(self.usage_file_path, "w") as f:
            await f.write(str(usage + float(price)))
            await f.close()

    @staticmethod
    def count_tokens_static(text):
        tokenizer = tiktoken.get_encoding("cl100k_base")
        res = tokenizer.encode(text)
        return len(res)
