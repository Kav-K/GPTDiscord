import base64

import pytest

from utils.image_attachments import image_references_to_discord_payload


def test_base64_images_become_attachments_and_urls_remain_links():
    image_bytes = b"\x89PNG\r\n\x1a\nimage-bytes"
    image_data = base64.b64encode(image_bytes).decode("ascii")

    content, files = image_references_to_discord_payload(
        [f"data:image/png;base64,{image_data}", "https://example.com/image.png"]
    )

    assert content == "[image 2](https://example.com/image.png)"
    assert len(files) == 1
    assert files[0].filename == "generated-1.png"
    assert files[0].fp.read() == image_bytes
    files[0].close()


def test_base64_image_only_message_has_no_base64_text_content():
    image_data = base64.b64encode(b"webp-image").decode("ascii")

    content, files = image_references_to_discord_payload(
        [f"data:image/webp;base64,{image_data}"]
    )

    assert content is None
    assert files[0].filename.endswith(".webp")
    files[0].close()


@pytest.mark.parametrize(
    "reference",
    ["data:text/html;base64,PGh0bWw+", "data:image/png;base64,%%%", "/etc/passwd"],
)
def test_invalid_image_references_fail_before_sending(reference):
    with pytest.raises(ValueError):
        image_references_to_discord_payload([reference])
