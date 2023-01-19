import discord

from services.environment_service import EnvService

BOT_NAME = EnvService.get_custom_bot_name()
class EmbedStatics:
    def __init__(self):
        pass

    @staticmethod
    def get_invalid_api_response_embed(e):
        embed = discord.Embed(
            title="The API returned an invalid response",
            description=f"**{e.status}: {e.message}**",
            color=discord.Color.red(),
        )
        return embed

    @staticmethod
    def get_invalid_value_embed(e):
        embed = discord.Embed(
            title="Invalid value",
            description=f"**{str(e)}**",
            color=discord.Color.red(),
        )
        return embed

    @staticmethod
    def get_general_error_embed(e):
        embed = discord.Embed(
            title="An error occurred",
            description=f"**{str(e)}**",
            color=discord.Color.red(),
        )
        return embed

    @staticmethod
    def generate_end_embed():
        embed = discord.Embed(
            title="Conversation Ended",
            description=f"This conversation has ended. You can start a new one with `/gpt converse`",
            color=0x808080,
        )
        return embed

    @staticmethod
    def generate_conversation_embed(conversation_threads, thread, opener, overrides):
        # Generate a nice looking embed for the above text
        embed = discord.Embed(
            title="Conversation started",
            description=f"Conversation started with {BOT_NAME}",
            color=0x808080,
        )
        embed.add_field(
            name="Model",
            value=f"The model used is **{conversation_threads[thread.id].model}**",
        )
        embed.add_field(
            name="Overrides",
            value=f"**temp={overrides['temperature']}**, **top_p={overrides['top_p']}**"
            f", **freq. penalty={overrides['frequency_penalty']}**, **pres. penalty={overrides['presence_penalty']}**\n",
        )
        embed.add_field(
            name="End the conversation",
            value="End the conversation by saying `end`, or clicking the red 'End Conversation' button\n\n",
            inline=False,
        )
        embed.add_field(
            name="Ignoring Messages",
            value="If you want GPT3 to ignore your messages, start your messages with `~`\n\n",
            inline=False,
        )
        return embed

    @staticmethod
    def generate_opener_embed(opener):
        embed = discord.Embed(
            title="Opening Prompt",
            description=f"{opener}",
            color=0x808080,
        )
        return embed