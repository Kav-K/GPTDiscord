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
    def value_to_openai_format(self, value, field) -> float:
        # TODO: fix the formula
        return value / 3.5

    async def send_language_detect_request(self, text):
        request = SIMPLEST_ANALYZE_REQUEST.copy()
        request["comment"]["text"] = text
        response = await self.client.analyze_comment(request)
        return response["detectedLanguages"]

    def to_openai_format(self, perspective_response: dict[dict, list]):
        matching_dict = {
            "TOXICITY": ["hate", "harassment", "violence"],
            "SEVERE_TOXICITY": ["hate", "harassment", "violence"],
            "IDENTITY_ATTACK": ["hate", "harassment"],
            "INSULT": ["hate", "harassment"],
            "PROFANITY": ["hate", "harassment"],
            "THREAT": ["hate", "harassment"],
            "SEXUALLY_EXPLICIT": ["sexual", "sexual/minors"],
        }
        openai_categories = [
            "sexual",
            "hate",
            "harassment",
            "self-harm",
            "sexual/minors",
            "hate/threatening",
            "violence/graphic",
            "self-harm/intent",
            "self-harm/instructions",
            "harassment/threatening",
            "violence",
        ]
        formatted_openai_response = {
            "id": "",
            "model": "perspective",
            "results": [
                {
                    "flagged": False,
                    "categories": {category: [False] for category in openai_categories},
                    "category_scores": {category: [] for category in openai_categories},
                }
            ],
        }
        for category, result in perspective_response["attributeScores"].items():
            value = result["summaryScore"]["value"]
            for openai_cateogry in matching_dict[category]:
                transformed_value = self.value_to_openai_format(value, category)
                formatted_openai_response["results"][0]["category_scores"][
                    openai_cateogry
                ].append(transformed_value)
                formatted_openai_response["results"][0]["categories"][
                    openai_cateogry
                ].append(transformed_value > 0.65)

        for category in openai_categories:
            if (
                formatted_openai_response["results"][0]["category_scores"][category]
                == []
            ):
                formatted_openai_response["results"][0]["category_scores"][
                    category
                ].append(0)
                formatted_openai_response["results"][0]["categories"][category].append(
                    False
                )
        for category in openai_categories:
            formatted_openai_response["results"][0]["category_scores"][category] = max(
                formatted_openai_response["results"][0]["category_scores"][category]
            )
            formatted_openai_response["results"][0]["categories"][category] = any(
                formatted_openai_response["results"][0]["categories"][category]
            )
        formatted_openai_response["results"][0]["flagged"] = any(
            formatted_openai_response["results"][0]["categories"].values()
        )
        return formatted_openai_response
