import os
import traceback

import aiohttp
import backoff

COUNTRY_CODES = {
    "BG": "Bulgarian",
    "CS": "Czech",
    "DA": "Danish",
    "DE": "German",
    "EL": "Greek",
    "EN": "English",
    "ES": "Spanish",
    "FI": "Finnish",
    "FR": "French",
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
    "ZH": "Chinese (simplified)",
}
class TranslationModel:

    def __init__(self):
        self.deepl_token = os.getenv("DEEPL_TOKEN")

    def backoff_handler(details):
        print(
            f"Backing off {details['wait']:0.1f} seconds after {details['tries']} tries calling function {details['target']} | "
            f"{details['exception'].status}: {details['exception'].message}"
        )

    @backoff.on_exception(
        backoff.expo,
        aiohttp.ClientResponseError,
        factor=3,
        base=5,
        max_tries=4,
        on_backoff=backoff_handler,
    )
    async def send_translate_request(self, text, translate_language):
        print("The text is: ", text)
        print("The language is: ", translate_language)
        print("The token is ", self.deepl_token)
        async with aiohttp.ClientSession(raise_for_status=True) as session:
            payload = {
                "text": text,
                "target_lang": translate_language,
            }
            # Instead of sending as json, we want to send as regular post params
            headers = {
                "Authorization": f"DeepL-Auth-Key {self.deepl_token}",
            }
            async with session.post(
                "https://api-free.deepl.com/v2/translate", params=payload, headers=headers
            ) as resp:
                response = await resp.json()
                print(response)

                try:
                    return response["translations"][0]["text"]
                except Exception:
                    print(response)
                    traceback.print_exc()
                    return response
    @staticmethod
    def get_all_country_names(lower=False):
        """Get a list of all the country names"""
        return list(COUNTRY_CODES.values()) if not lower else [name.lower() for name in COUNTRY_CODES.values()]

    @staticmethod
    def get_all_country_codes():
        """Get a list of all the country codes"""
        return list(COUNTRY_CODES.keys())

    @staticmethod
    def get_country_name_from_code(code):
        """Get the country name from the code"""
        return COUNTRY_CODES[code]

    @staticmethod
    def get_country_code_from_name(name):
        """Get the country code from the name"""
        for code, country_name in COUNTRY_CODES.items():
            if country_name.lower().strip() == name.lower().strip():
                return code