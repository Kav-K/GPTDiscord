import os

from services.environment_service import EnvService
import replicate


class ImageUnderstandingModel:
    def __init__(self):
        # Try to get the replicate API key from the environment
        self.replicate_key = EnvService.get_replicate_api_key()
        # Set the environment REPLICATE_API_TOKEN to the replicate API key
        if self.replicate_key:
            os.environ["REPLICATE_API_TOKEN"] = self.replicate_key
            self.key_set = True
        else:
            self.key_set = False

    def get_is_usable(self):
        return self.key_set

    def ask_image_question(self, prompt, filepath):
        output = replicate.run(
            "andreasjansson/blip-2:4b32258c42e9efd4288bb9910bc532a69727f9acd26aa08e175713a0a857a608",
            input={"image": open(filepath, "rb"), "question": prompt},
        )
        return output

    def get_image_caption(self, filepath):
        output = replicate.run(
            "andreasjansson/blip-2:4b32258c42e9efd4288bb9910bc532a69727f9acd26aa08e175713a0a857a608",
            input={"image": open(filepath, "rb"), "caption": True},
        )
        return output

    def get_image_stylistic_caption(self, filepath):
        output = replicate.run(
            "pharmapsychotic/clip-interrogator:a4a8bafd6089e1716b06057c42b19378250d008b80fe87caa5cd36d40c1eda90",
            input={"image": open(filepath, "rb")}
        )
        return output
