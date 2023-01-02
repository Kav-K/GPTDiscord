import os
import sys
from pathlib import Path
from dotenv import load_dotenv


# <app/bin/main.py>/../../
def app_root_path():
    try:
        return Path(sys.argv[0]).resolve().parents[1]
    except:
        return Path()

# None will let direnv do its' thing
env_paths = [Path() / ".env", app_root_path() / "etc/environment", None]

for env_path in env_paths:
    print("Loading environment from " + str(env_path))
    load_dotenv(dotenv_path=env_path)


class EnvService:
    # To be expanded upon later!
    def __init__(self):
        self.env = {}

    @staticmethod
    def environment_path_with_fallback(env_name, relative_fallback = None):
        dir = os.getenv(env_name)
        if dir != None:
            return Path(dir).resolve()

        if relative_fallback:
            app_relative = (app_root_path() / relative_fallback).resolve()
            if app_relative.exists():
                return app_relative

        return Path()

    @staticmethod
    def get_allowed_guilds():
        # ALLOWED_GUILDS is a comma separated list of guild ids
        # It can also just be one guild ID
        # Read these allowed guilds and return as a list of ints
        try:
            allowed_guilds = os.getenv("ALLOWED_GUILDS")
        except:
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
    def get_allowed_roles():
        # ALLOWED_ROLES is a comma separated list of string roles
        # It can also just be one role
        # Read these allowed roles and return as a list of strings
        try:
            allowed_roles = os.getenv("ALLOWED_ROLES")
        except:
            allowed_roles = None

        if allowed_roles is None:
            raise ValueError(
                "ALLOWED_ROLES is not defined properly in the environment file!"
                "Please copy your server's role and put it into ALLOWED_ROLES in the .env file."
                'For example a line should look like: `ALLOWED_ROLES="Admin"`'
            )

        allowed_roles = (
            allowed_roles.split(",") if "," in allowed_roles else [allowed_roles]
        )
        return allowed_roles
