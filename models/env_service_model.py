from dotenv import load_dotenv

load_dotenv()
import os


class EnvService:
    # To be expanded upon later!
    def __init__(self):
        self.env = {}

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
    
    @staticmethod
    def get_welcome_message():
        # WELCOME_MESSAGE is a default string used to welcome new members to the server if GPT3 is not available.
        #The string can be blank but this is not advised. If a string cannot be found in the .env file, the below string is used.
        #The string is DMd to the new server member as part of an embed.
        try:
            welcome_message = os.getenv("WELCOME_MESSAGE")
        except:
            welcome_message = "Hi there! Welcome to our Discord server!"
       return welcome_message
