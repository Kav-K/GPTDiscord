from pathlib import Path

import aiofiles
from transformers import GPT2TokenizerFast


class UsageService:
    def __init__(self, data_dir: Path):
        self.usage_file_path = data_dir / "usage.txt"
        # If the usage.txt file doesn't currently exist in the directory, create it and write 0.00 to it.
        if not self.usage_file_path.exists():
            with self.usage_file_path.open("w") as f:
                f.write("0.00")
                f.close()
        self.tokenizer = GPT2TokenizerFast.from_pretrained("gpt2")

    async def get_price(
        self,
        tokens_used,
        prompt_tokens=None,
        completion_tokens=None,
        embeddings=False,
        chatgpt=False,
        gpt4=False,
    ):
        tokens_used = int(tokens_used)
        if gpt4:
            price = (tokens_used / 1000) * 0.05 # This is a very rough estimate
            return price
        elif chatgpt:
            price = (tokens_used / 1000) * 0.002
            return price
        elif not embeddings:
            price = (tokens_used / 1000) * 0.02
        else:
            price = (tokens_used / 1000) * 0.0004
        return price

    async def update_usage(
        self,
        tokens_used,
        prompt_tokens=None,
        completion_tokens=None,
        embeddings=False,
        chatgpt=False,
        gpt4=False,
    ):
        tokens_used = int(tokens_used)
        if gpt4:
            price = (tokens_used / 1000) * 0.05 # This is a very rough estimate..
        elif chatgpt:
            price = (tokens_used / 1000) * 0.002
        elif not embeddings:
            price = (tokens_used / 1000) * 0.02
        else:
            price = (tokens_used / 1000) * 0.0004
        usage = await self.get_usage()
        print(
            f"Cost -> Old: {str(usage)} | New: {str(usage + float(price))}, used {str(float(price))} credits"
        )
        # Do the same as above but with aiofiles
        async with aiofiles.open(self.usage_file_path, "w") as f:
            await f.write(str(usage + float(price)))
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
        res = self.tokenizer(text)["input_ids"]
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
        tokenizer = GPT2TokenizerFast.from_pretrained("gpt2")
        res = tokenizer(text)["input_ids"]
        return len(res)
