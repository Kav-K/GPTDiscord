import asyncio
from pathlib import Path
import tempfile

import discord
from discord.ext import pages
from yt_dlp import YoutubeDL

from models.embed_statics_model import EmbedStatics
from models.check_model import UrlCheck
from models.openai_model import OpenAIAPIError
from services.environment_service import EnvService
from services.text_service import TextService
from utils.api_errors import openai_error_message

USER_INPUT_API_KEYS = EnvService.get_user_input_api_keys()
USER_KEY_DB = EnvService.get_api_db()
AUDIO_EXTENSIONS = {
    ".flac",
    ".mp3",
    ".mp4",
    ".mpeg",
    ".mpga",
    ".m4a",
    ".ogg",
    ".wav",
    ".webm",
}
TRANSCRIPT_PAGE_SIZE = 3500


async def transcribe_and_respond(model, ctx, file, temperature, user_api_key):
    progress = await ctx.respond(embed=EmbedStatics.build_transcribe_progress_embed())
    try:
        response = await model.send_transcription_request(
            file, temperature, user_api_key
        )
        if len(response) > TRANSCRIPT_PAGE_SIZE:
            paginator = pages.Paginator(
                pages=[
                    discord.Embed(
                        title=f"Transcription Page {index // TRANSCRIPT_PAGE_SIZE + 1}",
                        description=response[index : index + TRANSCRIPT_PAGE_SIZE],
                    )
                    for index in range(0, len(response), TRANSCRIPT_PAGE_SIZE)
                ],
                timeout=None,
                author_check=False,
            )
            await paginator.respond(ctx.interaction)
            await progress.delete()
            return
        await progress.edit(embed=EmbedStatics.build_transcribe_success_embed(response))
    except Exception as error:
        message = (
            openai_error_message(error)
            if isinstance(error, OpenAIAPIError)
            else "Transcription failed. Check the file and try again."
        )
        await progress.edit(embed=EmbedStatics.build_transcribe_failed_embed(message))


class TranscribeService(discord.Cog, name="TranscribeService"):
    """Transcribe uploaded audio/video and audio extracted from YouTube."""

    def __init__(self, bot, model, usage_service):
        super().__init__()
        self.bot = bot
        self.model = model
        self.usage_service = usage_service

    async def transcribe_link_command(self, ctx, link, temperature):
        user_api_key = None
        if USER_INPUT_API_KEYS:
            user_api_key = await TextService.get_user_api_key(
                ctx.user.id, ctx, USER_KEY_DB
            )
            if not user_api_key:
                return
        await ctx.defer()
        try:
            supported = await asyncio.wait_for(UrlCheck.check_youtube_link(link), 15)
        except Exception:
            supported = False
        if not supported:
            await ctx.respond(
                "Please upload a valid YouTube link. Other links are not implemented yet."
            )
            return

        # Every invocation owns its directory, including simultaneous requests
        # from the same user. The downloaded media is removed on every exit.
        with tempfile.TemporaryDirectory(prefix="gptdiscord-transcribe-") as directory:
            output = Path(directory) / "audio.mp3"
            options = {
                "format": "bestaudio/best",
                "outtmpl": str(Path(directory) / "audio.%(ext)s"),
                "quiet": True,
                "noplaylist": True,
                "socket_timeout": 20,
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ],
            }

            def download():
                with YoutubeDL(options) as downloader:
                    downloader.download([link])

            try:
                await asyncio.to_thread(download)
                file = discord.File(output)
            except Exception:
                await ctx.respond(
                    "Failed to download YouTube audio. Check the link and FFmpeg installation, then try again."
                )
                return
            try:
                await transcribe_and_respond(
                    self.model, ctx, file, temperature, user_api_key
                )
            finally:
                file.close()

    async def transcribe_file_command(self, ctx, file, temperature):
        user_api_key = None
        if USER_INPUT_API_KEYS:
            user_api_key = await TextService.get_user_api_key(
                ctx.user.id, ctx, USER_KEY_DB
            )
            if not user_api_key:
                return
        await ctx.defer()
        content_type = (file.content_type or "").lower()
        if Path(file.filename).suffix.lower() not in AUDIO_EXTENSIONS or (
            content_type
            and not content_type.startswith(
                ("audio/", "video/", "application/octet-stream")
            )
        ):
            await ctx.respond("Please upload a valid audio/video file.")
            return
        await transcribe_and_respond(self.model, ctx, file, temperature, user_api_key)
