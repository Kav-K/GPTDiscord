"""Explicitly opted-in, bounded live smoke checks; never collected by pytest.

Run with credentials already in the process environment:
    python -m tests.live_openai_smoke --run-live

Use --features chat,embeddings to select a subset. Each feature makes at most
one API request. Text output is capped at 512 tokens, web search at one tool
call, and image generation at one low-quality 1024px image. Paid usage includes
reasoning tokens and tool/image/audio fees. Reports contain no generated content,
provider error messages, credentials, request bodies, URLs, or response objects.
Run this module in a fresh process; importing it after the bot is rejected to
protect the bot's global settings database.
"""

import argparse
import asyncio
import base64
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

FEATURES = (
    "validate",
    "chat",
    "edit",
    "summary",
    "language",
    "json",
    "vision",
    "embeddings",
    "moderation",
    "image",
    "transcription",
    "web",
)
OUTPUT_TOKEN_LIMIT = 512
USAGE_FIELDS = {
    "prompt_tokens",
    "completion_tokens",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cached_tokens",
    "reasoning_tokens",
}


class SmokeIsolationError(RuntimeError):
    """The live runner must never reuse or close the bot's settings database."""


def numeric_usage(usage):
    """Keep only documented numeric counters, never arbitrary provider strings."""
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    if not isinstance(usage, dict):
        return {}
    result = {}
    for key, value in usage.items():
        if (
            key in USAGE_FIELDS
            and isinstance(value, int)
            and not isinstance(value, bool)
        ):
            result[key] = value
        elif key in {
            "input_tokens_details",
            "output_tokens_details",
            "prompt_tokens_details",
            "completion_tokens_details",
        }:
            details = numeric_usage(value)
            if details:
                result[key] = details
    return result


def failure_shape(error):
    """Classify failures without serializing their message, body, URL, or repr."""
    status = getattr(error, "status", None) or getattr(error, "status_code", None)
    code = getattr(error, "code", None)
    sdk_timeout = getattr(sys.modules.get("openai"), "APITimeoutError", ())
    if isinstance(error, SmokeIsolationError):
        category = "requires_fresh_process"
    elif code in {"insufficient_quota", "billing_hard_limit_reached"} or status == 402:
        category = "quota"
    elif status in {401, 403}:
        category = "authentication_or_permission"
    elif status == 429:
        category = "rate_limit"
    elif status == 404:
        category = "model_or_endpoint_unavailable"
    elif isinstance(error, (TimeoutError, asyncio.TimeoutError)) or isinstance(
        error, sdk_timeout
    ):
        category = "timeout"
    elif isinstance(error, AssertionError):
        category = "response_contract"
    elif isinstance(status, int) and status >= 400:
        category = "provider_request"
    else:
        category = "client_or_response"
    result = {"category": category}
    if isinstance(status, int):
        result["http_status"] = status
    return result


def web_result_shape(result):
    """Keep observed tool invocations separate from known billable searches."""
    assert result.text and result.citations
    assert result.web_tool_calls == 1
    assert 0 <= result.search_calls <= result.web_tool_calls
    assert 0 <= result.unknown_search_actions <= result.web_tool_calls
    assert result.search_calls + result.unknown_search_actions <= result.web_tool_calls
    return {
        "content_chars": len(result.text),
        "citations": len(result.citations),
        "web_tool_calls": result.web_tool_calls,
        "known_search_actions": result.search_calls,
        "unknown_search_actions": result.unknown_search_actions,
    }


class BoundedResponses:
    """Use ResponsesService's public client injection with tighter smoke limits."""

    def __init__(self, client):
        self.client = client
        self.responses = self

    async def create(self, **kwargs):
        kwargs["max_tool_calls"] = 1
        kwargs["max_output_tokens"] = OUTPUT_TOKEN_LIMIT
        return await self.client.responses.create(**kwargs)


async def _run_checks(model, usage, directory, selected, artifact_directory, emit):
    from PIL import Image
    import discord
    from openai import AsyncOpenAI

    from models.openai_model import Model, Models
    from services.environment_service import EnvService
    from services.responses_service import ResponsesService

    # Exercise the actual request implementation once, without backoff loops.
    model._post_json = Model._post_json.__wrapped__.__get__(model, Model)
    model.max_tokens = OUTPUT_TOKEN_LIMIT
    model.num_images = 1
    usage_events = []
    update_usage = usage.update_usage_from_response

    async def record_usage(counters, selected_model):
        usage_events.append(numeric_usage(counters))
        return await update_usage(counters, selected_model)

    usage.update_usage_from_response = record_usage

    def chat_shape(response):
        content = response["choices"][0]["message"]["content"]
        assert isinstance(content, str) and content.strip()
        return {"choices": len(response["choices"]), "content_chars": len(content)}

    async def chat():
        return chat_shape(
            await model.send_request("Reply only OK.", None, model=Models.DEFAULT)
        )

    async def validate():
        # Exercise /setup's actual key-validation path, which caps output at
        # 128 tokens and does not automatically retry or inherit server scopes.
        response = await Model.send_test_request(EnvService.get_openai_token())
        assert response.get("choices") and not response.get("error")
        usage_events.append(numeric_usage(response.get("usage")))
        return {"api_key_accepted": True, "choices": len(response["choices"])}

    async def language():
        result = await model.send_language_detect_request(
            "Bonjour, je vous souhaite une bonne journée.",
            "Identify the language of the user's text. Reply only with its English language name.",
        )
        shape = chat_shape(result)
        assert "french" in result["choices"][0]["message"]["content"].lower()
        return shape | {"expected_language": True}

    async def edit():
        return chat_shape(
            await model.send_edit_request(
                "Correct the spelling. Reply only with the corrected sentence.",
                "Helo world.",
            )
        )

    async def summary():
        model.model = Models.LOW_USAGE_MODEL
        return chat_shape(
            await model.send_summary_request(
                "User: My favorite color is blue.\nAssistant: I will remember.\nSummarize this in one sentence."
            )
        )

    async def json_check():
        result = await model.send_chatgpt_chat_request(
            [SimpleNamespace(text='Return exactly {"ok": true}.', image_urls=None)],
            Models.LOW_USAGE_MODEL,
            "GPTie",
            "SmokeUser",
            system_prompt_override="Follow the user's JSON instruction.",
            respond_json=True,
            max_tokens_override=OUTPUT_TOKEN_LIMIT,
        )
        assert result == {"ok": True}
        return {"json_object": True, "expected_boolean": True}

    async def vision():
        fixture = io.BytesIO()
        Image.new("RGB", (64, 64), "red").save(fixture, format="PNG")
        reference = (
            "data:image/png;base64," + base64.b64encode(fixture.getvalue()).decode()
        )
        result = await model.send_chatgpt_chat_request(
            [
                SimpleNamespace(
                    text="What is the background color? Reply with one color word.",
                    image_urls=[reference],
                )
            ],
            Models.LOW_USAGE_MODEL,
            "GPTie",
            "SmokeUser",
            system_prompt_override="Describe the supplied benign test image.",
            max_tokens_override=OUTPUT_TOKEN_LIMIT,
        )
        shape = chat_shape(result)
        assert "red" in result["choices"][0]["message"]["content"].lower()
        return shape | {"fixture_dimensions": [64, 64], "expected_color": True}

    async def embeddings():
        vector = await model.send_embedding_request("A harmless smoke test.")
        assert len(vector) == 1536 and all(
            isinstance(value, (float, int)) for value in vector
        )
        return {"dimensions": len(vector)}

    async def moderation():
        result = await model.send_moderations_request("Hello. Have a pleasant day.")
        assert result.get("results") and result["results"][0]["flagged"] is False
        return {"results": len(result["results"]), "benign_fixture_flagged": False}

    async def image():
        attachment, paths = await model.send_image_request(
            SimpleNamespace(guild=None),
            "A simple blue circle on a white background, flat icon.",
            "low",
            "1024x1024",
            None,
        )
        try:
            with Image.open(attachment.fp) as generated:
                dimensions = list(generated.size)
            assert len(paths) == 1 and dimensions == [1024, 1024]
            artifact_directory.mkdir(parents=True, exist_ok=True)
            artifact = artifact_directory / "generated-image.png"
            shutil.copyfile(paths[0], artifact)
            return {
                "images": len(paths),
                "dimensions": dimensions,
                "artifact_path": str(artifact),
            }
        finally:
            attachment.close()

    async def transcription():
        if not shutil.which("ffmpeg"):
            return {"skipped": "local_speech_fixture_unavailable"}
        audio_path = directory / "speech.wav"
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "flite=text=Hello from the Discord bot.:voice=slt",
            "-t",
            "3",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(audio_path),
        ]
        try:
            await asyncio.to_thread(
                subprocess.run,
                command,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
            )
        except (subprocess.SubprocessError, OSError):
            return {"skipped": "local_speech_fixture_unavailable"}
        attachment = discord.File(str(audio_path), filename="speech.wav")
        try:
            text = await model.send_transcription_request(
                attachment, temperature_override=0
            )
            assert isinstance(text, str) and "hello" in text.lower()
            artifact_directory.mkdir(parents=True, exist_ok=True)
            artifact = artifact_directory / "speech.wav"
            shutil.copyfile(audio_path, artifact)
            return {
                "artifact_path": str(artifact),
                "transcript_chars": len(text),
                "expected_greeting": True,
                "audio_max_seconds": 3,
            }
        finally:
            attachment.close()

    async def web():
        async with AsyncOpenAI(
            api_key=EnvService.get_openai_token(),
            base_url=EnvService.get_openai_base_url(),
            organization=EnvService.get_openai_organization(),
            project=EnvService.get_openai_project(),
            timeout=90,
            max_retries=0,
        ) as client:
            result = await ResponsesService(client=BoundedResponses(client)).search(
                "Search the official Python website and cite its homepage. Reply with one short sentence.",
                Models.LOW_USAGE_MODEL,
            )
        shape = web_result_shape(result)
        usage_events.append(numeric_usage(result.usage))
        return shape

    checks = {
        "validate": validate,
        "chat": chat,
        "edit": edit,
        "summary": summary,
        "language": language,
        "json": json_check,
        "vision": vision,
        "embeddings": embeddings,
        "moderation": moderation,
        "image": image,
        "transcription": transcription,
        "web": web,
    }
    outcomes = []
    blocked = None
    for feature in selected:
        if blocked:
            outcome = {"feature": feature, "status": "skipped", "reason": blocked}
            outcomes.append(outcome)
            emit(outcome)
            continue
        usage_start = len(usage_events)
        started = time.monotonic()
        outcome = {"feature": feature}
        try:
            shape = await asyncio.wait_for(
                checks[feature](), timeout=180 if feature == "image" else 120
            )
            if "skipped" in shape:
                outcome.update(status="skipped", reason=shape["skipped"])
            else:
                outcome.update(status="passed", shape=shape)
        except Exception as error:
            failure = failure_shape(error)
            outcome.update(status="failed", **failure)
            if failure["category"] in {
                "quota",
                "authentication_or_permission",
                "rate_limit",
            }:
                blocked = "blocked_by_" + failure["category"]
        outcome["latency_seconds"] = round(time.monotonic() - started, 3)
        if usage_events[usage_start:]:
            outcome["usage"] = usage_events[usage_start:]
        outcomes.append(outcome)
        emit(outcome)
    return {
        "checks": outcomes,
        "output_token_limit": OUTPUT_TOKEN_LIMIT,
        "image_limit": 1,
        "web_tool_call_limit": 1,
    }


async def run_live(selected, artifact_directory=None):
    """Set storage isolation before imports; credentials remain in memory only."""
    if "models.openai_model" in sys.modules:
        raise SmokeIsolationError("Run live smoke checks in a fresh process.")
    artifact_directory = Path(
        artifact_directory or tempfile.mkdtemp(prefix="gptdiscord-live-artifacts-")
    ).resolve()
    report_stream = sys.stdout

    def emit(outcome):
        print(json.dumps(outcome, sort_keys=True), file=report_stream, flush=True)

    with tempfile.TemporaryDirectory(prefix="gptdiscord-live-smoke-") as storage:
        directory = Path(storage)
        overrides = {
            "DATA_DIR": storage,
            "SHARE_DIR": storage,
            "IMAGE_SAVE_PATH": str(directory / "images"),
            "PYTHON_DOTENV_DISABLED": "1",
        }
        previous = {key: os.environ.get(key) for key in overrides}
        os.environ.update(overrides)
        previous_logging = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        try:
            # Old startup helpers print import diagnostics. Keep the report JSON-only.
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                from models.openai_model import Model, SETTINGS_DB
                from services.usage_service import UsageService

                try:
                    usage = UsageService(directory)
                    model = Model(usage)
                    return await _run_checks(
                        model, usage, directory, selected, artifact_directory, emit
                    )
                finally:
                    SETTINGS_DB.close()
        finally:
            logging.disable(previous_logging)
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-live",
        action="store_true",
        help="Explicitly authorize the bounded paid API checks.",
    )
    parser.add_argument(
        "--features",
        default=",".join(FEATURES),
        help="Comma-separated subset of feature names.",
    )
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        help="Keep generated image/audio fixtures here; defaults to a new temporary artifacts directory.",
    )
    args = parser.parse_args(argv)
    if not args.run_live:
        parser.error("Live checks require --run-live; no request was made.")
    selected = list(dict.fromkeys(args.features.split(",")))
    if not selected or any(feature not in FEATURES for feature in selected):
        parser.error("Unknown feature. Choose from: " + ",".join(FEATURES))
    if not (os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_TOKEN")):
        print(json.dumps({"status": "blocked", "reason": "missing_openai_credentials"}))
        return 2
    try:
        report = asyncio.run(run_live(selected, args.artifact_dir))
    except Exception as error:
        report = {"status": "failed", **failure_shape(error)}
    print(json.dumps(report, sort_keys=True))
    return int(
        report.get("status") == "failed"
        or any(check["status"] == "failed" for check in report.get("checks", []))
    )


if __name__ == "__main__":
    raise SystemExit(main())
