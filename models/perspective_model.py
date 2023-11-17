from services.environment_service import EnvService

import aiohttp
import asyncio
from aiolimiter import AsyncLimiter


class languageNotSupportedByAttribute(Exception):
    pass


ratelimiter = AsyncLimiter(EnvService.get_max_perspective_requests_per_second(), 1.0)


class PerspectiveClient:
    """
    A client for the Perspective API.

    Args:
        api_key (str): The API key for the Perspective API.

    Attributes:
        api_key (str): The API key for the Perspective API.
        base_url (str): The base URL for the Perspective API.

    """

    def __init__(self, api_key):
        self.api_key = api_key
        self.base_url = (
            "https://commentanalyzer.googleapis.com/v1alpha1/comments:analyze"
        )

    async def analyze_comment(self, input_data):
        headers = {"Content-Type": "application/json"}
        params = {"key": self.api_key}
        async with ratelimiter:
            async with aiohttp.ClientSession() as session:
                try:
                    async with session.post(
                        self.base_url, headers=headers, json=input_data, params=params
                    ) as response:
                        response_json = await response.json()
                        response.raise_for_status()
                        return response_json
                except aiohttp.ClientResponseError as e:
                    if (
                        e.status == 400
                        and response_json["error"]["details"][0].get("errorType")
                        == "LANGUAGE_NOT_SUPPORTED_BY_ATTRIBUTE"
                    ):
                        raise languageNotSupportedByAttribute
                    else:
                        raise


SIMPLEST_ANALYZE_REQUEST = {
    "comment": {"text": ""},
    "requestedAttributes": {
        "TOXICITY": {},
    },
    "languages": [],
    "doNotStore": "true",
}

ANALYZE_REQUEST = {
    "comment": {"text": ""},
    "requestedAttributes": {
        "TOXICITY": {},
        "SEVERE_TOXICITY": {},
        "IDENTITY_ATTACK": {},
        "INSULT": {},
        "PROFANITY": {},
        "THREAT": {},
        "SEXUALLY_EXPLICIT": {},
    },
    "languages": [],
    "doNotStore": "true",
}

ANALYZE_REQUEST_NOT_EN = {
    "comment": {"text": ""},
    "requestedAttributes": {
        "TOXICITY": {},
        "SEVERE_TOXICITY": {},
        "IDENTITY_ATTACK": {},
        "INSULT": {},
        "PROFANITY": {},
        "THREAT": {},
    },
    "languages": [],
    "doNotStore": "true",
}


class Model:
    def __init__(self) -> None:
        self.client = PerspectiveClient(EnvService.get_perspective_api_key())

    async def send_moderations_request(self, text):
        try:
            request = ANALYZE_REQUEST.copy()
            request["comment"]["text"] = text
            response = await self.client.analyze_comment(request)
            return response
        except languageNotSupportedByAttribute:
            try:
                request = ANALYZE_REQUEST_NOT_EN.copy()
                request["comment"]["text"] = text
                response = await self.client.analyze_comment(request)
                return response
            except languageNotSupportedByAttribute:
                raise

    async def send_language_detect_request(self, text):
        request = SIMPLEST_ANALYZE_REQUEST.copy()
        request["comment"]["text"] = text
        response = await self.client.analyze_comment(request)
        return response["detectedLanguages"]