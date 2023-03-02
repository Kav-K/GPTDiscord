import traceback

import aiohttp
import discord
from discord.ext import pages

from models.deepl_model import TranslationModel
from models.embed_statics_model import EmbedStatics
from services.environment_service import EnvService
from services.text_service import TextService

ALLOWED_GUILDS = EnvService.get_allowed_guilds()
USER_INPUT_API_KEYS = EnvService.get_user_input_api_keys()
USER_KEY_DB = EnvService.get_api_db()
class TranscribeService(discord.Cog, name="TranscribeService"):
    """Cog containing translation commands and retrieval of transcribe services"""

    def __init__(
        self,
        bot,
        model,
        usage_service,
    ):
        super().__init__()
        self.bot = bot
        self.model = model
        self.usage_service = usage_service

    async def transcribe_file_command(self, ctx: discord.ApplicationContext, file: discord.Attachment, temperature: float):
        # Check if this discord file is an instance of mp3, mp4, mpeg, mpga, m4a, wav, or webm.

        user_api_key = None
        if USER_INPUT_API_KEYS:
            user_api_key = await TextService.get_user_api_key(
                ctx.user.id, ctx, USER_KEY_DB
            )
            if not user_api_key:
                return

        if not file.filename.endswith(('.mp3', '.mp4', '.mpeg', '.mpga', '.m4a', '.wav', '.webm')):
            await ctx.respond("Please upload a valid audio/video file.")
            return

        # Also check the file metadata in case it is actually an audio/video file but with a weird ending
        if not file.content_type.startswith(('audio/', 'video/')):
            await ctx.respond("Please upload a valid audio/video file.")
            return

        response_message = await ctx.respond(embed=EmbedStatics.build_transcribe_progress_embed())

        try:

            response = await self.model.send_transcription_request(file, temperature, user_api_key)

            if len(response) > 4080:
                # Chunk the response into 2048 character chunks, each an embed page
                chunks = [response[i:i+2048] for i in range(0, len(response), 2048)]
                embed_pages = []
                for chunk in chunks:
                    embed_pages.append(discord.Embed(title="Transcription Page {}".format(len(embed_pages) + 1), description=chunk))


                paginator = pages.Paginator(
                    pages=embed_pages,
                    timeout=None,
                    author_check=False,
                )

                await paginator.respond(ctx.interaction)
                await response_message.delete_original_response()
                return

            await response_message.edit_original_response(embed=EmbedStatics.build_transcribe_success_embed(response))
        except Exception as e:
            await response_message.edit_original_response(embed=EmbedStatics.build_transcribe_failed_embed(str(e)))

