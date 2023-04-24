import base64
import json
import os

import aiohttp

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

        self.google_cloud_project_id = EnvService.get_google_cloud_project_id()
        self.google_cloud_api_key = EnvService.get_google_search_api_key()

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
            input={"image": open(filepath, "rb")},
        )
        return output

    async def do_image_ocr(self, filepath):
        # Read the image file and encode it in base64 format
        if not self.google_cloud_api_key:
            return "None"
        with open(filepath, 'rb') as image_file:
            encoded_image = base64.b64encode(image_file.read()).decode('utf-8')

        # Prepare the JSON payload
        payload = {
            "requests": [
                {
                    "image": {"content": encoded_image},
                    "features": [{"type": "TEXT_DETECTION"}]
                }
            ]
        }

        header = {
            "Content-Type": "application/json; charset=utf-8",
        }

        url = f"https://vision.googleapis.com/v1/images:annotate?key={self.google_cloud_api_key}"

        # Send the async request
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=header, data=json.dumps(payload)) as response:
                result = await response.json()

                if response.status == 200:
                    # Get fullTextAnnotation
                    full_text_annotation = result.get('responses', [])[0].get('fullTextAnnotation')

                    if full_text_annotation:
                        extracted_text = full_text_annotation.get('text')

                        # Return the extracted text
                        return extracted_text
                    else:
                        return ""
                else:
                    raise Exception(
                        f"Google Cloud Vision API returned an error. Status code: {response.status}, Error: {result}")
