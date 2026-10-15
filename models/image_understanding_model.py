import base64
import json
import os
from importlib.util import find_spec

import aiohttp

from services.environment_service import EnvService


class ImageUnderstandingModel:
    def __init__(self):
        # Try to get the replicate API key from the environment
        self.replicate_key = EnvService.get_replicate_api_key()
        # Set the environment REPLICATE_API_TOKEN to the replicate API key
        if self.replicate_key:
            os.environ["REPLICATE_API_TOKEN"] = self.replicate_key
            self.key_set = find_spec("replicate") is not None
        else:
            self.key_set = False

        self.google_cloud_project_id = EnvService.get_google_cloud_project_id()
        self.google_cloud_api_key = EnvService.get_google_search_api_key()

    def get_is_usable(self):
        return self.key_set

    @staticmethod
    def _run(model, filepath, **inputs):
        # Replicate is an optional fallback for models without native image input.
        import replicate

        with open(filepath, "rb") as image:
            return replicate.run(model, input={"image": image, **inputs})

    def ask_image_question(self, prompt, filepath):
        output = self._run(
            "andreasjansson/blip-2:4b32258c42e9efd4288bb9910bc532a69727f9acd26aa08e175713a0a857a608",
            filepath,
            question=prompt,
        )
        return output

    def get_llava_answer(self, prompt, filepath):
        output = self._run(
            "yorickvp/llava-13b:e272157381e2a3bf12df3a8edd1f38d1dbd736bbb7437277c8b34175f8fce358",
            filepath,
            prompt=prompt,
            temperature=0.2,
            top_p=1,
            max_tokens=1024,
        )
        return output

    def get_minigpt_answer(self, prompt, filepath):
        output = self._run(
            "daanelson/minigpt-4:b96a2f33cc8e4b0aa23eacfce731b9c41a7d9466d9ed4e167375587b54db9423",
            filepath,
            prompt=prompt,
        )
        return output

    def get_image_caption(self, filepath):
        output = self._run(
            "andreasjansson/blip-2:4b32258c42e9efd4288bb9910bc532a69727f9acd26aa08e175713a0a857a608",
            filepath,
            caption=True,
        )
        return output

    def get_image_stylistic_caption(self, filepath):
        output = self._run(
            "pharmapsychotic/clip-interrogator:a4a8bafd6089e1716b06057c42b19378250d008b80fe87caa5cd36d40c1eda90",
            filepath,
        )
        return output

    async def do_image_ocr(self, filepath):
        # Read the image file and encode it in base64 format
        if not self.google_cloud_api_key:
            return "None"
        with open(filepath, "rb") as image_file:
            encoded_image = base64.b64encode(image_file.read()).decode("utf-8")

        # Prepare the JSON payload
        payload = {
            "requests": [
                {
                    "image": {"content": encoded_image},
                    "features": [{"type": "TEXT_DETECTION"}],
                }
            ]
        }

        header = {
            "Content-Type": "application/json; charset=utf-8",
        }

        url = f"https://vision.googleapis.com/v1/images:annotate?key={self.google_cloud_api_key}"

        # Send the async request
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=header, data=json.dumps(payload)
            ) as response:
                result = await response.json()

                if response.status == 200:
                    # Get fullTextAnnotation
                    full_text_annotation = result.get("responses", [])[0].get(
                        "fullTextAnnotation"
                    )

                    if full_text_annotation:
                        extracted_text = full_text_annotation.get("text")

                        # Return the extracted text
                        return extracted_text
                    else:
                        return ""
                else:
                    raise Exception(
                        f"Google Cloud Vision API returned an error. Status code: {response.status}, Error: {result}"
                    )
