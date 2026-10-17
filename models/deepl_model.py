import os

import aiohttp
import backoff

from services.environment_service import EnvService

COUNTRY_CODES = {
    "EN": "English",
    "ES": "Spanish",
    "FR": "French",
    "ZH": "Chinese (simplified)",
    "BG": "Bulgarian",
    "CS": "Czech",
    "DA": "Danish",
    "DE": "German",
    "EL": "Greek",
    "FI": "Finnish",
    "HU": "Hungarian",
    "ID": "Indonesian",
    "IT": "Italian",
    "JA": "Japanese",
    "LT": "Lithuanian",
    "LV": "Latvian",
    "NL": "Dutch",
    "PL": "Polish",
    "PT": "Portuguese",
    "RO": "Romanian",
    "RU": "Russian",
    "SK": "Slovak",
    "SV": "Swedish",
    "TR": "Turkish",
    "UK": "Ukrainian",
}


class TranslationModel:
    def __init__(self):
        self.deepl_token = os.getenv("DEEPL_TOKEN")

    def backoff_handler(details):
        print(
            f"DeepL retry {details['tries']} after HTTP {details['exception'].status}"
        )

    @backoff.on_exception(
        backoff.expo,
        aiohttp.ClientResponseError,
        factor=3,
        base=5,
        max_tries=4,
        on_backoff=backoff_handler,
        giveup=lambda error: error.status != 429 and error.status < 500,
    )
    async def send_translate_request(self, text, translate_language, formality):
        async with aiohttp.ClientSession(
            raise_for_status=True, timeout=aiohttp.ClientTimeout(total=60)
        ) as session:
            payload = {
                "text": [text],
                "target_lang": translate_language,
                "formality": "default" if formality is None else formality,
            }
            headers = {
                "Authorization": f"DeepL-Auth-Key {self.deepl_token}",
            }
            host = (
                "api-free.deepl.com"
                if (self.deepl_token or "").endswith(":fx")
                else "api.deepl.com"
            )
            async with session.post(
                f"https://{host}/v2/translate",
                json=payload,
                headers=headers,
                proxy=EnvService.get_http_proxy(f"https://{host}/v2/translate"),
            ) as resp:
                response = await resp.json()
                try:
                    return (
                        response["translations"][0]["text"],
                        response["translations"][0]["detected_source_language"],
                    )
                except (KeyError, IndexError, TypeError) as error:
                    raise ValueError(
                        "DeepL returned an invalid translation response"
                    ) from error

    @staticmethod
    def get_all_country_names(lower=False):
        """Get a list of all the country names"""
        return (
            list(COUNTRY_CODES.values())
            if not lower
            else [name.lower() for name in COUNTRY_CODES.values()]
        )

    @staticmethod
    def get_all_country_codes():
        """Get a list of all the country codes"""
        return list(COUNTRY_CODES.keys())

    @staticmethod
    def get_country_name_from_code(code):
        """Get the country name from the code"""
        try:
            return COUNTRY_CODES[code]
        except KeyError:
            return "Unknown Language"

    @staticmethod
    def get_country_code_from_name(name):
        """Get the country code from the name"""
        for code, country_name in COUNTRY_CODES.items():
            if country_name.lower().strip() == name.lower().strip():
                return code
