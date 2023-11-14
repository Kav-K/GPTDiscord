import os
import sys
import traceback
from pathlib import Path
from typing import Union

from dotenv import load_dotenv
from sqlitedict import SqliteDict


def app_root_path():
    app_path = Path(sys.argv[0]).resolve()
    try:
        if app_path.parent.name == "bin":  # Installed in unixy hierachy
            return app_path.parents[1]
    except IndexError:
        pass
    return app_path.parent


# None will let direnv do its' thing
env_paths = [Path(".env"), app_root_path() / "etc/environment", None]

for env_path in env_paths:
    print("Loading environment from " + str(env_path))
    load_dotenv(dotenv_path=env_path)


class EnvService:
    # To be expanded upon later!
    def __init__(self):
        self.env = {}

    @staticmethod
    def environment_path_with_fallback(env_name, relative_fallback=None):
        directory = os.getenv(env_name)
        if directory is not None:
            return Path(directory).resolve()

        if relative_fallback:
            app_relative = (app_root_path() / relative_fallback).resolve()
            if app_relative.exists():
                return app_relative

        return Path.cwd()

    @staticmethod
    def save_path():
        share_dir = os.getenv("SHARE_DIR")
        if share_dir is not None:
            return Path(share_dir)
        return app_root_path()

    @staticmethod
    def find_shared_file(file_name):
        share_file_paths = []
        share_dir = os.getenv("SHARE_DIR")
        if share_dir is not None:
            share_file_paths.append(Path(share_dir) / file_name)

        share_file_paths.extend(
            [
                app_root_path() / "share" / file_name,
                app_root_path() / file_name,
                Path(file_name),
            ]
        )

        for share_file_path in share_file_paths:
            if share_file_path.exists():
                return share_file_path.resolve()

        raise ValueError(f"Unable to find shared data file {file_name}")

    @staticmethod
    def get_allowed_guilds():
        # ALLOWED_GUILDS is a comma separated list of guild ids
        # It can also just be one guild ID
        # Read these allowed guilds and return as a list of ints
        try:
            allowed_guilds = os.getenv("ALLOWED_GUILDS")
        except Exception:
            allowed_guilds = None

        if allowed_guilds is None:
            raise ValueError(
                "ALLOWED_GUILDS is not defined properly in the environment file!"
                "Please copy your server's guild ID and put it into ALLOWED_GUILDS in the .env file."
                'For example a line should look like: `ALLOWED_GUILDS="971268468148166697"`'
            )

        allowed_guilds = (
            allowed_guilds.split(",") if "," in allowed_guilds else [allowed_guilds]
        )
        allowed_guilds = [int(guild) for guild in allowed_guilds]
        return allowed_guilds

    @staticmethod
    def get_admin_roles():
        # ADMIN_ROLES is a comma separated list of string roles
        # It can also just be one role
        # Read these allowed roles and return as a list of strings
        try:
            admin_roles = os.getenv("ADMIN_ROLES")
        except Exception:
            admin_roles = None

        if admin_roles is None:
            print(
                "ADMIN_ROLES is not defined properly in the environment file!"
                "Please copy your server's role and put it into ADMIN_ROLES in the .env file."
                'For example a line should look like: `ADMIN_ROLES="Admin"`'
            )
            print("Defaulting to allowing all users to use admin commands...")
            return [None]

        admin_roles = (
            admin_roles.lower().split(",")
            if "," in admin_roles
            else [admin_roles.lower()]
        )
        return admin_roles

    @staticmethod
    def get_dalle_roles():
        # DALLE_ROLES is a comma separated list of string roles
        # It can also just be one role
        # Read these allowed roles and return as a list of strings
        try:
            dalle_roles = os.getenv("DALLE_ROLES")
        except Exception:
            dalle_roles = None

        if dalle_roles is None:
            print(
                "DALLE_ROLES is not defined properly in the environment file!"
                "Please copy your server's role and put it into DALLE_ROLES in the .env file."
                'For example a line should look like: `DALLE_ROLES="Dalle"`'
            )
            print("Defaulting to allowing all users to use Dalle commands...")
            return [None]

        dalle_roles = (
            dalle_roles.lower().split(",")
            if "," in dalle_roles
            else [dalle_roles.lower()]
        )
        return dalle_roles

    @staticmethod
    def get_translator_roles():
        # TRANSLATOR_ROLES is a comma separated list of string roles
        # It can also just be one role
        # Read these allowed roles and return as a list of strings
        try:
            translator_roles = os.getenv("TRANSLATOR_ROLES")
        except Exception:
            translator_roles = None

        if translator_roles is None:
            print(
                "TRANSLATOR_ROLES is not defined properly in the environment file!"
                "Please copy your server's role and put it into TRANSLATOR in the .env file."
                'For example a line should look like: `TRANSLATOR_ROLES="Translate"`'
            )
            print("Defaulting to allowing all users to use Translator commands...")
            return [None]

        translator_roles = (
            translator_roles.lower().split(",")
            if "," in translator_roles
            else [translator_roles.lower()]
        )
        return translator_roles

    @staticmethod
    def get_search_roles():
        # SEARCH_ROLES is a comma separated list of string roles
        # It can also just be one role
        # Read these allowed roles and return as a list of strings
        try:
            search_roles = os.getenv("SEARCH_ROLES")
        except Exception:
            search_roles = None

        if search_roles is None:
            print(
                "SEARCH_ROLES is not defined properly in the environment file!"
                "Please copy your server's role and put it into SEARCH in the .env file."
                'For example a line should look like: `SEARCH_ROLES="Translate"`'
            )
            print("Defaulting to allowing all users to use Search commands...")
            return [None]

        search_roles = (
            search_roles.lower().split(",")
            if "," in search_roles
            else [search_roles.lower()]
        )
        return search_roles

    @staticmethod
    def get_gpt_roles():
        # GPT_ROLES is a comma separated list of string roles
        # It can also just be one role
        # Read these allowed roles and return as a list of strings
        try:
            gpt_roles = os.getenv("GPT_ROLES")
        except Exception:
            gpt_roles = None

        if gpt_roles is None:
            print(
                "GPT_ROLES is not defined properly in the environment file!"
                "Please copy your server's role and put it into GPT_ROLES in the .env file."
                'For example a line should look like: `GPT_ROLES="Gpt"`'
            )
            print("Defaulting to allowing all users to use GPT commands...")
            return [None]

        gpt_roles = (
            gpt_roles.lower().strip().split(",")
            if "," in gpt_roles
            else [gpt_roles.lower()]
        )
        return gpt_roles

    @staticmethod
    def get_index_roles():
        # INDEX_ROLES is a comma separated list of string roles
        # It can also just be one role
        # Read these allowed roles and return as a list of strings
        try:
            index_roles = os.getenv("INDEX_ROLES")
        except Exception:
            traceback.print_exc()
            index_roles = None

        if index_roles is None:
            print(
                "INDEX_ROLES is not defined properly in the environment file!"
                "Please copy your server's role and put it into INDEX_ROLES in the .env file."
                'For example a line should look like: `INDEX_ROLES="Gpt"`'
            )
            print("Defaulting to allowing all users to use Index commands...")
            return [None]

        index_roles = (
            index_roles.lower().strip().split(",")
            if "," in index_roles
            else [index_roles.lower()]
        )
        return index_roles

    @staticmethod
    def get_channel_chat_roles():
        # CHANNEL_CHAT_ROLES is a comma separated list of string roles
        # It can also just be one role
        # Read these allowed roles and return as a list of strings
        try:
            cc_roles = os.getenv("CHANNEL_CHAT_ROLES")
        except Exception:
            cc_roles = None

        if cc_roles is None:
            print(
                "CHANNEL_CHAT_ROLES is not defined properly in the environment file!"
                "Please copy your server's role and put it into CHANNEL_CHAT_ROLES in the .env file."
                'For example a line should look like: `CHANNEL_CHAT_ROLES="Gpt"`'
            )
            print(
                "Defaulting to allowing all users to make conversations in full channels..."
            )
            return [None]

        cc_roles = (
            cc_roles.lower().strip().split(",")
            if "," in cc_roles
            else [cc_roles.lower()]
        )
        return cc_roles

    @staticmethod
    def get_channel_instruction_roles():
        # CHANNEL_INSTRUCTION_ROLES is a comma separated list of string roles
        # It can also just be one role
        # Read these allowed roles and return as a list of strings
        try:
            cc_roles = os.getenv("CHANNEL_INSTRUCTION_ROLES")
        except Exception:
            cc_roles = None

        if cc_roles is None:
            print(
                "CHANNEL_INSTRUCTION_ROLES is not defined properly in the environment file!"
                "Please copy your server's role and put it into CHANNEL_INSTRUCTION_ROLES in the .env file."
                'For example a line should look like: `CHANNEL_INSTRUCTION_ROLES="Gpt"`'
            )
            print(
                "Defaulting to allowing all users to set instructions for channels..."
            )
            return [None]

        cc_roles = (
            cc_roles.lower().strip().split(",")
            if "," in cc_roles
            else [cc_roles.lower()]
        )
        return cc_roles

    @staticmethod
    def get_welcome_message():
        # WELCOME_MESSAGE is a default string used to welcome new members to the server if GPT is not available.
        # The string can be blank but this is not advised. If a string cannot be found in the .env file, the below string is used.
        # The string is DMd to the new server member as part of an embed.
        try:
            welcome_message = os.getenv("WELCOME_MESSAGE")
        except Exception:
            welcome_message = "Hi there! Welcome to our Discord server!"
        return welcome_message

    @staticmethod
    def get_moderations_alert_channel():
        # MODERATIONS_ALERT_CHANNEL is a channel id where moderation alerts are sent to
        # The string can be blank but this is not advised. If a string cannot be found in the .env file, the below string is used.
        try:
            moderations_alert_channel = os.getenv("MODERATIONS_ALERT_CHANNEL")
        except Exception:
            moderations_alert_channel = None
        return moderations_alert_channel

    @staticmethod
    def get_user_input_api_keys():
        try:
            user_input_api_keys = os.getenv("USER_INPUT_API_KEYS")
            if user_input_api_keys.lower().strip() == "true":
                return True
            return False
        except Exception:
            return False

    @staticmethod
    def get_premoderate():
        try:
            pre_moderate = os.getenv("PRE_MODERATE")
            if pre_moderate.lower().strip() == "true":
                return True
            return False
        except Exception:
            return False

    @staticmethod
    def get_force_language():
        languages = "Arabic (ar), Chinese (zh), Czech (cs), Dutch (nl), English (en), French (fr), German (de), Hindi (hi), Hinglish (hi-Latn), Indonesian (id), Italian (it), Japanese (ja), Korean (ko), Polish (pl), Portuguese (pt), Russian (ru), Spanish (es), Swedish (sv)"
        available_languages = ["ar", "zh", "cs", "nl", "en", "fr", "de", "hi", "hi-Latn", "id", "it", "ja", "ko", "pl", "pt", "ru", "es", "sv"]
        try:
            force_language = os.getenv("FORCE_LANGUAGE", "none").lower().strip()
            if force_language in available_languages:
                if force_language != "en" and EnvService.get_language_detect_service() == "openai":
                    print(f"FORCE_LANGUAGE is set to {force_language} but the language detection service service is set to OpenAI. OpenAI does not support language detection for {force_language}. Language detection will be disabled. You can change the language detection service witht he LANGUAGE_DETECT_SERVICE environment variable.")
                    return None
                return force_language
            elif force_language.lower().strip() not in available_languages and force_language != "none":
                print(f"FORCE_LANGUAGE is not defined properly in the environment file! The language {force_language} is not supported. The available languages are: {languages} if you use perspective, and English (en) if you use openai.")
                return None
            elif force_language == "none" and EnvService.get_force_english():
                return "en"
            return None
        except Exception:
            return None

    @staticmethod
    def get_force_english():
        try:
            force_english = os.getenv("FORCE_ENGLISH")
            if force_english.lower().strip() == "true":
                return True
            return False
        except Exception:
            return False

    @staticmethod
    def get_custom_bot_name():
        try:
            custom_bot_name = os.getenv("CUSTOM_BOT_NAME") + ": "
            return custom_bot_name
        except Exception:
            return "GPTie: "

    @staticmethod
    def get_health_service_enabled():
        try:
            user_input_api_keys = os.getenv("HEALTH_SERVICE_ENABLED")
            if user_input_api_keys.lower().strip() == "true":
                return True
            return False
        except Exception:
            return False

    @staticmethod
    def get_bot_is_taggable():
        try:
            user_input_api_keys = os.getenv("BOT_TAGGABLE")
            if user_input_api_keys.lower().strip() == "true":
                return True
            return False
        except Exception:
            return False

    @staticmethod
    def get_user_key_db_path() -> Union[Path, None]:
        try:
            user_key_db_path = os.getenv("USER_KEY_DB_PATH")
            if user_key_db_path is None:
                return None
            return Path(user_key_db_path)
        except Exception:
            return None

    @staticmethod
    def get_api_db():
        user_input_api_keys = EnvService.get_user_input_api_keys()
        user_key_db = None
        if user_input_api_keys:
            print(
                "This server was configured to enforce user input API keys. Doing the required database setup now"
            )
            # Get user_key_db from environment variable
            user_key_db_path = EnvService.get_user_key_db_path()
            # Check if user_key_db_path is valid
            if not user_key_db_path:
                print(
                    "No user key database path was provided. Defaulting to user_key_db.sqlite"
                )
                user_key_db_path = EnvService.find_shared_file("user_key_db.sqlite")
            else:
                # append "user_key_db.sqlite" to USER_KEY_DB_PATH if it doesn't already end with .sqlite
                if not user_key_db_path.match("*.sqlite"):
                    # append "user_key_db.sqlite" to USER_KEY_DB_PATH
                    user_key_db_path = user_key_db_path / "user_key_db.sqlite"
            user_key_db = SqliteDict(user_key_db_path)
            print("Retrieved/created the user key database")
            return user_key_db
        return user_key_db

    @staticmethod
    def get_bypass_roles():
        # GPT_ROLES is a comma separated list of string roles
        # It can also just be one role
        # Read these allowed roles and return as a list of strings
        try:
            bypass_roles = os.getenv("CHAT_BYPASS_ROLES")
        except Exception:
            bypass_roles = None

        if bypass_roles is None:
            print(
                "CHAT_BYPASS_ROLES is not defined properly in the environment file!"
                "Please copy your server's role and put it into CHAT_BYPASS_ROLES in the .env file."
                'For example a line should look like: `CHAT_BYPASS_ROLES="bypass"`'
            )
            print("Defaulting to allowing NO ONE to bypass chat moderation")
            return [None]

        bypass_roles = (
            bypass_roles.lower().strip().split(",")
            if "," in bypass_roles
            else [bypass_roles.lower()]
        )
        return bypass_roles

    @staticmethod
    def get_deepl_token():
        try:
            deepl_token = os.getenv("DEEPL_TOKEN")
            return deepl_token
        except Exception:
            return None

    @staticmethod
    def get_github_token():
        try:
            github_token = os.getenv("GITHUB_TOKEN")
            return github_token
        except Exception:
            return None

    @staticmethod
    def get_openai_token():
        try:
            openai_token = os.getenv("OPENAI_TOKEN")
            return openai_token
        except Exception:
            raise ValueError(
                "OPENAI_TOKEN is not defined properly in the environment file! The bot cannot start without this token."
            )

    @staticmethod
    def get_wolfram_api_key():
        try:
            openai_token = os.getenv("WOLFRAM_API_KEY")
            return openai_token
        except Exception:
            print(
                "WOLFRAM_API_KEY is not defined properly in the environment file! The bot cannot use /internet chat's wolfram functionalities without this"
            )
            return None

    @staticmethod
    def get_openai_organization():
        try:
            openai_org = os.getenv("OPENAI_ORGANIZATION")
            return openai_org
        except Exception:
            return None

    @staticmethod
    def get_google_search_api_key():
        try:
            google_search_api_key = os.getenv("GOOGLE_SEARCH_API_KEY")
            return google_search_api_key
        except Exception:
            return None

    @staticmethod
    def get_replicate_api_key():
        try:
            replicate_key = os.getenv("REPLICATE_API_KEY")
            return replicate_key
        except Exception:
            return None

    @staticmethod
    def get_e2b_api_key():
        try:
            e2b_key = os.getenv("E2B_API_KEY")
            return e2b_key
        except Exception:
            return None

    @staticmethod
    def get_google_search_engine_id():
        try:
            google_search_engine_id = os.getenv("GOOGLE_SEARCH_ENGINE_ID")
            return google_search_engine_id
        except Exception:
            return None

    @staticmethod
    def get_pinecone_region():
        try:
            pinecone_region = os.getenv("PINECONE_REGION")
            return pinecone_region
        except Exception:
            return "us-west1-gcp"

    @staticmethod
    def get_max_search_price():
        try:
            search_price = float(os.getenv("MAX_SEARCH_PRICE"))
            return search_price
        except Exception:
            return 1.00

    @staticmethod
    def get_max_deep_compose_price():
        try:
            deep_compose_price = float(os.getenv("MAX_DEEP_COMPOSE_PRICE"))
            return deep_compose_price
        except Exception:
            return 3.00

    @staticmethod
    def get_google_cloud_project_id():
        try:
            google_cloud_project_id = os.getenv("GOOGLE_CLOUD_PROJECT_ID")
            return google_cloud_project_id
        except Exception:
            return None
    
    @staticmethod
    def get_moderation_service():
        try:
            moderation_service = os.getenv("MODERATION_SERVICE", "openai")
            return moderation_service
        except Exception:
            return "openai"
    
    @staticmethod
    def get_language_detect_service():
        try:
            moderation_model = os.getenv("LANGUAGE_DETECT_SERVICE", EnvService.get_moderation_service())
            return moderation_model
        except Exception:
            return EnvService.get_moderation_service()

    @staticmethod
    def get_perspective_api_key() -> Union[str, None]:
        try:
            perspective_api_key = os.getenv("PERSPECTIVE_API_KEY")
            if perspective_api_key is None:
                raise ValueError("PERSPECTIVE_API_KEY is not defined properly in the environment file! The bot cannot start without this token.")
            return perspective_api_key
        except Exception:
            return None
    
    @staticmethod
    def get_max_perspective_requests_per_second() -> float:
        try:
            max_perspective_requests_per_second = float(os.getenv("MAX_PERSPECTIVE_REQUESTS_PER_SECOND"), 1.0)
            return max_perspective_requests_per_second
        except Exception:
            return 1.0