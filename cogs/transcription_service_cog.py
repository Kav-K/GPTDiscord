import asyncio
import traceback
from functools import partial
from pathlib import Path

import aiohttp
import discord
from discord.ext import pages
from pytube import YouTube

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
        # Make the "audiotemp" folder if it doesn't exist, using pathlib
        Path("audiotemp").mkdir(parents=True, exist_ok=True)
    async def transcribe_link_command(self, ctx: discord.ApplicationContext, link:str, temperature: float):
        # Check if this discord file is an instance of mp3, mp4, mpeg, mpga, m4a, wav, or webm.
        await ctx.defer()

        user_api_key = None
        if USER_INPUT_API_KEYS:
            user_api_key = await TextService.get_user_api_key(
                ctx.user.id, ctx, USER_KEY_DB
            )
            if not user_api_key:
                return

        if "youtube" in link:
            # We need to download the youtube video and save it to a temporary file
            yt = YouTube(link)

            # Delete audiotemp/{str(ctx.user.id)}temp.mp3 if it already exists
            if Path("audiotemp/{}temp.mp3".format(str(ctx.user.id))).exists():
                Path("audiotemp/{}temp.mp3".format(str(ctx.user.id))).unlink()
            print("before call")
            try:
                file_path = await asyncio.get_running_loop().run_in_executor(None, partial(yt.streams.filter().first().download, output_path="audiotemp", filename="{}temp".format(str(ctx.user.id))))
            except Exception as e:
                traceback.print_exc()
                await ctx.respond("Failed to download youtube video. Please try again later. "+str(e))
                return

            print("after call the file path was" + file_path)
        else:
            await ctx.respond("Please upload a valid youtube link. Other links are not implemented yet")
            return

        # Load the file object from the file_path
        file = discord.File(file_path)

        response_message = await ctx.respond(embed=EmbedStatics.build_transcribe_progress_embed())

        try:

            response = await self.model.send_transcription_request(file, temperature, user_api_key)
            print(response)

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
                await response_message.delete()
                return

            await response_message.edit(embed=EmbedStatics.build_transcribe_success_embed(response))
        except Exception as e:
            await response_message.edit(embed=EmbedStatics.build_transcribe_failed_embed(str(e)))


    async def transcribe_file_command(
        self,
        ctx: discord.ApplicationContext,
        file: discord.Attachment,
        temperature: float,
    ):
        # Check if this discord file is an instance of mp3, mp4, mpeg, mpga, m4a, wav, or webm.
        await ctx.defer()

        user_api_key = None
        if USER_INPUT_API_KEYS:
            user_api_key = await TextService.get_user_api_key(
                ctx.user.id, ctx, USER_KEY_DB
            )
            if not user_api_key:
                return

        if not file.filename.endswith(
            (".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm")
        ):
            await ctx.respond("Please upload a valid audio/video file.")
            return

        # Also check the file metadata in case it is actually an audio/video file but with a weird ending
        if not file.content_type.startswith(("audio/", "video/")):
            await ctx.respond("Please upload a valid audio/video file.")
            return

        response_message = await ctx.respond(
            embed=EmbedStatics.build_transcribe_progress_embed()
        )


        try:
            response = await self.model.send_transcription_request(
                file, temperature, user_api_key
            )

            if len(response) > 4080:
                # Chunk the response into 2048 character chunks, each an embed page
                chunks = [response[i : i + 2048] for i in range(0, len(response), 2048)]
                embed_pages = []
                for chunk in chunks:
                    embed_pages.append(discord.Embed(title="Transcription Page {}".format(len(embed_pages) + 1), description=chunk))


                paginator = pages.Paginator(
                    pages=embed_pages,
                    timeout=None,
                    author_check=False,
                )

                await paginator.respond(ctx.interaction)

            await response_message.edit(
                embed=EmbedStatics.build_transcribe_success_embed(response)
            )
        except Exception as e:
            traceback.print_exc()
            await response_message.edit(
                embed=EmbedStatics.build_transcribe_failed_embed(str(e))
            )
