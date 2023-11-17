import asyncio
import os
import random
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import discord

from models.openai_model import Model as OpenAIModel
from models.perspective_model import (
    Model as PerspectiveModel,
    languageNotSupportedByAttribute,
)
from services.environment_service import EnvService
from services.usage_service import UsageService


class ModerationModel:
    def __init__(self, type: str, language_detect_type=None):
        if type not in ["openai", "perspective"]:
            raise ValueError("Invalid model type")
        self.type = type
        usage_service = UsageService(Path(os.environ.get("DATA_DIR", os.getcwd())))

        if language_detect_type == None:
            self.language_detect_type = self.type
        if self.type == "openai" or language_detect_type == "openai":
            self.openaiModel = OpenAIModel(usage_service)
        if self.type == "perspective" or language_detect_type == "perspective":
            self.perspectiveModel = PerspectiveModel()

    async def send_language_detect_request(self, text, pretext, language) -> bool:
        """
        Sends a language detection request using either the OpenAI or Perspective API.

        Args:
            text (str): The text to analyze.
            pretext (str): The pretext to analyze.
            language (str): The language to detect.

        Returns:
            A tuple containing a boolean indicating whether the language was detected, and the first detected language (if any).
        """
        # false is not in language
        # true is in language
        if self.language_detect_type == "openai":
            response = await self.openaiModel.send_language_detect_request(
                text, pretext
            )
            content: str = response["choices"][0]["text"]
            return ((not "false" in content.lower().strip() and language == "en"), None)
        elif self.language_detect_type == "perspective":
            try:
                response = await self.perspectiveModel.send_language_detect_request(
                    text
                )
            except languageNotSupportedByAttribute: # perspective doesn't support this language
                return False, None
            return ((len(response) == 1 and response[0] == language), response[0])

    async def send_moderations_request(self, text, override_model=None) -> dict:
        """
        Sends a moderation request for the given text to the appropriate model.

        Args:
            text (str): The text to moderate.
            override_model (str, optional): Overrides the default model for moderation. Defaults to None.

        Returns:
            dict: The moderation results.
        """
        if self.type == "openai" or override_model == "openai":
            result = await self.openaiModel.send_moderations_request(text)
            return result
        elif self.type == "perspective" or override_model == "perspective":
            result = await self.perspectiveModel.send_moderations_request(text)
            return result


moderation_model_type = EnvService.get_moderation_service()
language_detect_model_type = EnvService.get_language_detect_service()
model = ModerationModel(moderation_model_type)

LANGUAGE_MATCHING_DICT = {
    "ar": "Arabic",
    "zh": "Chinese",
    "cs": "Czech",
    "nl": "Dutch",
    "en": "English",
    "fr": "French",
    "de": "German",
    "hi": "Hindi",
    "hi-Latn": "Hinglish",
    "id": "Indonesian",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "pl": "Polish",
    "pt": "Portuguese",
    "ru": "Russian",
    "es": "Spanish",
    "sv": "Swedish",
}

LANGUAGE_MODERATED_MESSAGES = {
    "ar": {
        "title": "تم تعديل رسالتك",
        "description": "اكتشفت أنظمة الإشراف التلقائية لدينا أن رسالتك لم تكن باللغة {lang} وتم حذفها. يرجى مراجعة القواعد.",
    },
    "zh": {
        "title": "您的消息已被审查",
        "description": "我们的自动审查系统检测到您的消息不是用{lang}撰写的，已被删除。请检查规则。",
    },
    "cs": {
        "title": "Vaše zpráva byla moderována",
        "description": "Naše automatické moderační systémy zjistily, že vaše zpráva nebyla v {lang} a byla smazána. Přečtěte si prosím pravidla.",
    },
    "nl": {
        "title": "Uw bericht is gemodereerd",
        "description": "Onze automatische moderatiesystemen hebben gedetecteerd dat uw bericht niet in {lang} was en is verwijderd. Gelieve de regels te herzien.",
    },
    "en": {
        "title": "Your message was moderated",
        "description": "Our automatic moderation systems detected that your message was not in {lang} and has been deleted. Please review the rules.",
    },
    "fr": {
        "title": "Votre message a été modéré",
        "description": "Nos systèmes de modération automatiques ont détecté que votre message n'était pas en {lang} et a été supprimé. Veuillez consulter les règles.",
    },
    "de": {
        "title": "Ihre Nachricht wurde moderiert",
        "description": "Unsere automatischen Moderationssysteme haben festgestellt, dass Ihre Nachricht nicht in {lang} war und wurde gelöscht. Bitte überprüfen Sie die Regeln.",
    },
    "hi": {
        "title": "आपका संदेश मॉडरेट किया गया है",
        "description": "हमारी स्वचालित मॉडरेशन प्रणालियों ने पाया कि आपका संदेश {lang} में नहीं था और इसे हटा दिया गया है। कृपया नियमों की समीक्षा करें।",
    },
    "hi-Latn": {
        "title": "Aapka sandesh moderated kiya gaya hai",
        "description": "Hamari swachalit moderation pranaliyon ne paya ki aapka sandesh {lang} mein nahi tha aur ise hata diya gaya hai. Kripya niyamon ki samiksha karen.",
    },
    "id": {
        "title": "Pesan Anda telah dimoderasi",
        "description": "Sistem moderasi otomatis kami mendeteksi bahwa pesan Anda tidak dalam {lang} dan telah dihapus. Silakan tinjau aturan-aturan tersebut.",
    },
    "it": {
        "title": "Il tuo messaggio è stato moderato",
        "description": "I nostri sistemi di moderazione automatica hanno rilevato che il tuo messaggio non era in {lang} ed è stato eliminato. Si prega di rivedere le regole.",
    },
    "ja": {
        "title": "あなたのメッセージはモデレートされました",
        "description": "当社の自動モデレーションシステムは、あなたのメッセージが{lang}ではないことを検出し、削除されました。ルールを確認してください。",
    },
    "ko": {
        "title": "귀하의 메시지가 조정되었습니다",
        "description": "우리의 자동 조정 시스템은 귀하의 메시지가 {lang}이 아니라고 감지하여 삭제되었습니다. 규칙을 검토하십시오.",
    },
    "pl": {
        "title": "Twoja wiadomość została zmodyfikowana",
        "description": "Nasze automatyczne systemy moderacji wykryły, że Twoja wiadomość nie była w {lang} i została usunięta. Proszę zapoznać się z zasadami.",
    },
    "pt": {
        "title": "Sua mensagem foi moderada",
        "description": "Nossos sistemas de moderação automáticos detectaram que sua mensagem não estava em {lang} e foi excluída. Por favor, revise as regras.",
    },
    "ru": {
        "title": "Ваше сообщение было отмодерировано",
        "description": "Наши автоматические системы модерации обнаружили, что ваше сообщение не на {lang} и было удалено. Пожалуйста, ознакомьтесь с правилами.",
    },
    "es": {
        "title": "Tu mensaje ha sido moderado",
        "description": "Nuestros sistemas de moderación automáticos detectaron que tu mensaje no estaba en {lang} y ha sido eliminado. Por favor, revisa las reglas.",
    },
    "sv": {
        "title": "Ditt meddelande har modererats",
        "description": "Våra automatiska modereringssystem upptäckte att ditt meddelande inte var på {lang} och har tagits bort. Vänligen se över reglerna.",
    },
}


class ModerationResult:
    WARN = "warn"
    DELETE = "delete"
    NONE = "none"


class ModerationOptions:
    WARN = "warn"
    DELETE = "delete"
    RESET = "reset"

    OPTIONS = [WARN, DELETE, RESET]


class ThresholdSet:
    def __init__(self, h_t, hv_t, sh_t, s_t, sm_t, v_t, vg_t):
        """A set of thresholds for the OpenAI moderation endpoint

        Args:
            h_t (float): hate
            hv_t (float): hate/violence
            sh_t (float): self-harm
            s_t (float): sexual
            sm_t (float): sexual/minors
            v_t (float): violence
            vg_t (float): violence/graphic
        """
        self.keys = [
            "hate",
            "hate/threatening",
            "self-harm",
            "sexual",
            "sexual/minors",
            "violence",
            "violence/graphic",
        ]
        self.thresholds = [
            h_t,
            hv_t,
            sh_t,
            s_t,
            sm_t,
            v_t,
            vg_t,
        ]

    # The string representation is just the keys alongside the threshold values

    def __str__(self):
        """ "key": value format"""
        # "key": value format
        return ", ".join([f"{k}: {v}" for k, v in zip(self.keys, self.thresholds)])

    def moderate(self, text, response_message):
        category_scores = response_message["results"][0]["category_scores"]
        flagged = response_message["results"][0]["flagged"]

        for category, threshold in zip(self.keys, self.thresholds):
            threshold = float(threshold)
            if category_scores[category] > threshold:
                return (True, flagged)
        return (False, flagged)


class PerspectiveThresholdSet:
    def __init__(self, tx, s_tx, i_a, i, p, tr, s_e):
        """A set of thresholds for the Perspective moderation endpoint

        Args:
            tx (float): toxicity
            s_tx (float): severe toxicity
            i_a (float): identity attack
            i (float): insult
            p (float): profanity
            tr (float): threat
            s_e (float): sexually explicit
        """

        self.keys = [
            "TOXICITY",
            "SEVERE_TOXICITY",
            "IDENTITY_ATTACK",
            "INSULT",
            "PROFANITY",
            "THREAT",
            "SEXUALLY_EXPLICIT",
        ]
        self.thresholds = [
            tx,
            s_tx,
            i_a,
            i,
            p,
            tr,
            s_e,
        ]

    # The string representation is just the keys alongside the threshold values

    def __str__(self):
        """ "key": value format"""
        # "key": value format
        return ", ".join([f"{k}: {v}" for k, v in zip(self.keys, self.thresholds)])

    def moderate(self, text, response_message):
        attribute_scores = response_message["attributeScores"]
        flagged = False  # not applicable for perspective

        for category, threshold in zip(self.keys, self.thresholds):
            threshold = float(threshold)
            #            if attribute_scores[category]["summaryScore"]["value"] > threshold:
            if (
                attribute_scores.get(category)
                and attribute_scores[category]["summaryScore"]["value"] > threshold
            ):
                return (True, flagged)
        return (False, flagged)


class Moderation:
    # Moderation service data
    moderation_queues = {}
    moderation_alerts_channel = EnvService.get_moderations_alert_channel()
    moderation_enabled_guilds = []
    moderation_tasks = {}
    moderations_launched = []

    def __init__(self, message, timestamp):
        self.message = message
        self.timestamp = timestamp

    @staticmethod
    def build_moderation_embed():
        # Create a discord embed to send to the user when their message gets moderated
        embed = discord.Embed(
            title="Your message was moderated",
            description="Our automatic moderation systems detected that your message was inappropriate and has been deleted. Please review the rules.",
            colour=discord.Colour.red(),
        )
        # Set the embed thumbnail
        embed.set_thumbnail(
            url="https://i.imgur.com/2oL8JSp.png"
            if model.type == "openai"
            else "https://i.imgur.com/MLi8bOn.png"
        )
        embed.set_footer(
            text="If you think this was a mistake, please contact the server admins."
        )
        return embed

    @staticmethod
    def build_safety_blocked_message():
        # Create a discord embed to send to the user when their message gets moderated
        embed = discord.Embed(
            title="Your request was blocked by the safety system",
            description="Our automatic moderation systems detected that your request was inappropriate and it has not been sent. Please review the usage guidelines.",
            colour=discord.Colour.red(),
        )
        # Set the embed thumbnail
        embed.set_thumbnail(url="https://i.imgur.com/2oL8JSp.png")
        embed.set_footer(
            text="If you think this was a mistake, please contact the server admins."
        )
        return embed

    @staticmethod
    def build_non_language_message(language, detected_language=None):
        # Create a discord embed to send to the user when their message gets moderated
        title = LANGUAGE_MODERATED_MESSAGES.get(detected_language, LANGUAGE_MODERATED_MESSAGES["en"])["title"]
        description = LANGUAGE_MODERATED_MESSAGES.get(detected_language, LANGUAGE_MODERATED_MESSAGES["en"])["description"]
        description = description.format(lang=LANGUAGE_MATCHING_DICT.get(language, "English"))
        embed = discord.Embed(
            title=title,
            description=description,
            colour=discord.Colour.red(),
        )
        # Set the embed thumbnail
        embed.set_thumbnail(
            url="https://i.imgur.com/2oL8JSp.png"
            if model.language_detect_type == "openai"
            else "https://i.imgur.com/MLi8bOn.png"
        )
        embed.set_footer(
            text="If you think this was a mistake, please contact the server admins."
        )
        return embed

    @staticmethod
    async def force_language_and_respond(text, pretext, ctx, language: str):
        response, detected_language = await model.send_language_detect_request(text, pretext, language)

        if not response:
            if isinstance(ctx, discord.Message):
                await ctx.reply(embed=Moderation.build_non_language_message(language, detected_language))
            else:
                await ctx.respond(embed=Moderation.build_non_language_message(language, detected_language))
            return False
        return True

    @staticmethod
    async def simple_moderate(text):
        return await model.send_moderations_request(text, override_model="openai")

    @staticmethod
    async def simple_moderate_and_respond(text, ctx):
        pre_mod_set = ThresholdSet(0.26, 0.26, 0.1, 0.95, 0.03, 0.95, 0.4)

        response = await Moderation.simple_moderate(text)
        flagged = (
            True
            if Moderation.determine_moderation_result(
                text, response, pre_mod_set, pre_mod_set
            )
            == ModerationResult.DELETE
            else False
        )

        if flagged:
            if isinstance(ctx, discord.Message):
                await ctx.reply(embed=Moderation.build_safety_blocked_message())
            else:
                await ctx.respond(embed=Moderation.build_safety_blocked_message())
            return True
        return False

    @staticmethod
    def build_admin_warning_message(
        moderated_message, deleted_message=None, timed_out=None
    ):
        embed = discord.Embed(
            title="Potentially unwanted message in the "
            + moderated_message.guild.name
            + " server",
            description=f"**Message from {moderated_message.author.mention}:** {moderated_message.content}",
            colour=discord.Colour.yellow(),
        )
        embed.set_footer(text=f"Used service {moderation_model_type}")
        link = f"https://discord.com/channels/{moderated_message.guild.id}/{moderated_message.channel.id}/{moderated_message.id}"
        embed.add_field(name="Message link", value=link, inline=False)
        if deleted_message:
            embed.add_field(
                name="Message deleted by: ", value=deleted_message, inline=False
            )
        if timed_out:
            embed.add_field(name="User timed out by: ", value=timed_out, inline=False)
        return embed

    @staticmethod
    def build_admin_moderated_message(
        moderated_message, response_message, user_kicked=None, timed_out=None
    ):
        direct_message_object = isinstance(moderated_message, discord.Message)
        moderated_message = (
            moderated_message if direct_message_object else moderated_message.message
        )

        # Create a discord embed to send to the user when their message gets moderated
        embed = discord.Embed(
            title="A message was moderated in the "
            + moderated_message.guild.name
            + " server",
            description=f"Message from {moderated_message.author.mention} was moderated: {moderated_message.content}",
            colour=discord.Colour.red(),
        )
        embed.set_footer(text=f"Used service {moderation_model_type}")
        # Get the link to the moderated message
        link = f"https://discord.com/channels/{response_message.guild.id}/{response_message.channel.id}/{response_message.id}"
        # set the link of the embed
        embed.add_field(name="Moderated message link", value=link, inline=False)
        if user_kicked:
            embed.add_field(name="User kicked by", value=user_kicked, inline=False)
        if timed_out:
            embed.add_field(name="User timed out by: ", value=timed_out, inline=False)
        return embed

    @staticmethod
    def determine_moderation_result(text, response, warn_set, delete_set):
        warn_result, flagged_warn = warn_set.moderate(text, response)
        delete_result, flagged_delete = delete_set.moderate(text, response)

        if delete_result:
            return ModerationResult.DELETE
        if warn_result:
            return ModerationResult.WARN
        return ModerationResult.NONE

    @staticmethod
    async def process_moderation_queue(
        moderation_queue,
        PROCESS_WAIT_TIME,
        EMPTY_WAIT_TIME,
        moderations_alert_channel,
        warn_set,
        delete_set,
    ):
        while True:
            try:
                if moderation_queue.empty():
                    await asyncio.sleep(EMPTY_WAIT_TIME)
                    continue

                # Get the next message from the queue
                to_moderate = await moderation_queue.get()

                # Check if the current timestamp is greater than the deletion timestamp
                if datetime.now().timestamp() > to_moderate.timestamp:
                    try:
                        response = await model.send_moderations_request(
                            to_moderate.message.content
                        )
                    except languageNotSupportedByAttribute:
                        # If the language is not supported, just ignore the message
                        continue
                    moderation_result = Moderation.determine_moderation_result(
                        to_moderate.message.content, response, warn_set, delete_set
                    )

                    if moderation_result == ModerationResult.DELETE:
                        # Take care of the flagged message
                        response_message = await to_moderate.message.reply(
                            embed=Moderation.build_moderation_embed()
                        )
                        # Do the same response as above but use an ephemeral message
                        await to_moderate.message.delete()

                        # Send to the moderation alert channel
                        if moderations_alert_channel:
                            response_message = await moderations_alert_channel.send(
                                embed=Moderation.build_admin_moderated_message(
                                    to_moderate, response_message
                                )
                            )
                            await response_message.edit(
                                view=ModerationAdminView(
                                    to_moderate.message,
                                    response_message,
                                    True,
                                    True,
                                    True,
                                )
                            )

                    elif moderation_result == ModerationResult.WARN:
                        response_message = await moderations_alert_channel.send(
                            embed=Moderation.build_admin_warning_message(
                                to_moderate.message
                            ),
                        )
                        # Attempt to react to the to_moderate.message with a warning icon
                        try:
                            await to_moderate.message.add_reaction("⚠️")
                        except discord.errors.Forbidden:
                            pass

                        await response_message.edit(
                            view=ModerationAdminView(
                                to_moderate.message, response_message
                            )
                        )

                else:
                    await moderation_queue.put(to_moderate)
                # Sleep for a short time before processing the next message
                # This will prevent the bot from spamming messages too quickly
                await asyncio.sleep(PROCESS_WAIT_TIME)
            except Exception:
                traceback.print_exc()


class ModerationAdminView(discord.ui.View):
    def __init__(
        self,
        message,
        moderation_message,
        nodelete=False,
        deleted_message=False,
        source_deleted=False,
    ):
        super().__init__(timeout=None)  # 1 hour interval to redo.
        component_number = 0
        self.message = message
        self.moderation_message = (moderation_message,)
        self.add_item(
            TimeoutUserButton(
                self.message,
                self.moderation_message,
                component_number,
                1,
                nodelete,
                source_deleted,
            )
        )
        component_number += 1
        self.add_item(
            TimeoutUserButton(
                self.message,
                self.moderation_message,
                component_number,
                6,
                nodelete,
                source_deleted,
            )
        )
        component_number += 1
        self.add_item(
            TimeoutUserButton(
                self.message,
                self.moderation_message,
                component_number,
                12,
                nodelete,
                source_deleted,
            )
        )
        component_number += 1
        self.add_item(
            TimeoutUserButton(
                self.message,
                self.moderation_message,
                component_number,
                24,
                nodelete,
                source_deleted,
            )
        )
        component_number += 1
        if not nodelete:
            self.add_item(
                DeleteMessageButton(
                    self.message, self.moderation_message, component_number
                )
            )
            component_number += 1
            self.add_item(
                ApproveMessageButton(
                    self.message, self.moderation_message, component_number
                )
            )
            component_number += 1
        if deleted_message:
            self.add_item(
                KickUserButton(self.message, self.moderation_message, component_number)
            )


class ApproveMessageButton(discord.ui.Button["ModerationAdminView"]):
    def __init__(self, message, moderation_message, current_num):
        super().__init__(
            style=discord.ButtonStyle.green, label="Approve", custom_id="approve_button"
        )
        self.message = message
        self.moderation_message = moderation_message
        self.current_num = current_num

    async def callback(self, interaction: discord.Interaction):
        # Remove reactions on the message, delete the moderation message
        await self.message.clear_reactions()
        await self.moderation_message[0].delete()


class DeleteMessageButton(discord.ui.Button["ModerationAdminView"]):
    def __init__(self, message, moderation_message, current_num):
        super().__init__(
            style=discord.ButtonStyle.danger,
            label="Delete Message",
            custom_id="delete_button",
        )
        self.message = message
        self.moderation_message = moderation_message
        self.current_num = current_num

    async def callback(self, interaction: discord.Interaction):
        # Get the user
        await self.message.delete()
        await interaction.response.send_message(
            "This message was deleted", ephemeral=True, delete_after=10
        )
        while isinstance(self.moderation_message, tuple):
            self.moderation_message = self.moderation_message[0]
        await self.moderation_message.edit(
            embed=Moderation.build_admin_warning_message(
                self.message, deleted_message=interaction.user.mention
            ),
            view=ModerationAdminView(
                self.message, self.moderation_message, nodelete=True
            ),
        )


class KickUserButton(discord.ui.Button["ModerationAdminView"]):
    def __init__(self, message, moderation_message, current_num):
        super().__init__(
            style=discord.ButtonStyle.danger, label="Kick User", custom_id="kick_button"
        )
        self.message = message
        self.moderation_message = moderation_message
        self.current_num = current_num

    async def callback(self, interaction: discord.Interaction):
        # Get the user and kick the user
        try:
            await self.message.author.kick(
                reason="You broke the server rules. Please rejoin and review the rules."
            )
        except Exception:
            pass
        await interaction.response.send_message(
            "This user was attempted to be kicked", ephemeral=True, delete_after=10
        )

        while isinstance(self.moderation_message, tuple):
            self.moderation_message = self.moderation_message[0]
        await self.moderation_message.edit(
            embed=Moderation.build_admin_moderated_message(
                self.message,
                self.moderation_message,
                user_kicked=interaction.user.mention,
            ),
            view=ModerationAdminView(
                self.message,
                self.moderation_message,
                nodelete=True,
                deleted_message=False,
                source_deleted=True,
            ),
        )


class TimeoutUserButton(discord.ui.Button["ModerationAdminView"]):
    def __init__(
        self, message, moderation_message, current_num, hours, nodelete, source_deleted
    ):
        super().__init__(
            style=discord.ButtonStyle.danger,
            label=f"Timeout {hours}h",
            custom_id="timeout_button" + str(random.randint(100000, 999999)),
        )
        self.message = message
        self.moderation_message = moderation_message
        self.hours = hours
        self.nodelete = nodelete
        self.current_num = current_num
        self.source_deleted = source_deleted

    async def callback(self, interaction: discord.Interaction):
        # Get the user id
        try:
            await self.message.delete()
        except Exception:
            pass

        try:
            await self.message.author.timeout(
                until=discord.utils.utcnow() + timedelta(hours=self.hours),
                reason="Breaking the server chat rules",
            )
        except Exception:
            traceback.print_exc()

        await interaction.response.send_message(
            f"This user was timed out for {self.hours} hour(s)",
            ephemeral=True,
            delete_after=10,
        )

        while isinstance(self.moderation_message, tuple):
            self.moderation_message = self.moderation_message[0]

        if not self.source_deleted:
            await self.moderation_message.edit(
                embed=Moderation.build_admin_warning_message(
                    self.message,
                    deleted_message=interaction.user.mention,
                    timed_out=interaction.user.mention,
                ),
                view=ModerationAdminView(
                    self.message, self.moderation_message, nodelete=True
                ),
            )
        else:
            await self.moderation_message.edit(
                embed=Moderation.build_admin_moderated_message(
                    self.message,
                    self.moderation_message,
                    timed_out=interaction.user.mention,
                ),
                view=ModerationAdminView(
                    self.message,
                    self.moderation_message,
                    nodelete=True,
                    deleted_message=True,
                    source_deleted=True,
                ),
            )
