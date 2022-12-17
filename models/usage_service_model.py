import os

from transformers import GPT2TokenizerFast


class UsageService:
    def __init__(self):
        # If the usage.txt file doesn't currently exist in the directory, create it and write 0.00 to it.
        if not os.path.exists("usage.txt"):
            with open("usage.txt", "w") as f:
                f.write("0.00")
                f.close()
        self.tokenizer = GPT2TokenizerFast.from_pretrained("gpt2")

    def update_usage(self, tokens_used):
        tokens_used = int(tokens_used)
        price = (tokens_used / 1000) * 0.02
        print("This request cost " + str(price) + " credits")
        usage = self.get_usage()
        print("The current usage is " + str(usage) + " credits")
        with open("usage.txt", "w") as f:
            f.write(str(usage + float(price)))
            f.close()

    def get_usage(self):
        with open("usage.txt", "r") as f:
            usage = float(f.read().strip())
            f.close()
        return usage

    def count_tokens(self, input):
        res = self.tokenizer(input)["input_ids"]
        return len(res)
