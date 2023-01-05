import os
from pathlib import Path

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

    def update_usage(self, tokens_used):
        tokens_used = int(tokens_used)
        price = (tokens_used / 1000) * 0.02
        usage = self.get_usage()
        print("The current usage is " + str(usage) + " credits")
        with self.usage_file_path.open("w") as f:
            f.write(str(usage + float(price)))
            f.close()

    def set_usage(self, usage):
        with self.usage_file_path.open("w") as f:
            f.write(str(usage))
            f.close()

    def get_usage(self):
        with self.usage_file_path.open("r") as f:
            usage = float(f.read().strip())
            f.close()
        return usage

    def count_tokens(self, input):
        res = self.tokenizer(input)["input_ids"]
        return len(res)

    def update_usage_image(self, image_size):
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

        usage = self.get_usage()

        with self.usage_file_path.open("w") as f:
            f.write(str(usage + float(price)))
            f.close()
