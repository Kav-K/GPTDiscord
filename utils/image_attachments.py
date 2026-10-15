"""Convert image API results to Discord attachments without exposing base64 text."""

import base64
from io import BytesIO

import discord


def image_references_to_discord_payload(references):
    files = []
    links = []
    extensions = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
    for number, reference in enumerate(references, start=1):
        if reference.startswith("data:"):
            header, encoded = reference.split(",", 1)
            mime_type = header.removeprefix("data:").removesuffix(";base64")
            if not header.endswith(";base64") or mime_type not in extensions:
                raise ValueError("The image API returned an unsupported image format.")
            image_bytes = base64.b64decode(encoded, validate=True)
            files.append(
                discord.File(
                    BytesIO(image_bytes),
                    filename=f"generated-{number}.{extensions[mime_type]}",
                )
            )
        elif reference.startswith(("https://", "http://")):
            links.append(f"[image {number}]({reference})")
        else:
            raise ValueError("The image API returned an unsupported image reference.")
    return " ".join(links) or None, files
