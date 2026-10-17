"""Shared code/search/index answer pagination must retain every character."""

import pytest

from models.embed_statics_model import EmbedStatics


@pytest.mark.parametrize("length", [0, 1, 3499, 3500, 3501, 7000, 7001, 14003])
def test_chat_pagination_retains_full_output_within_discord_embed_bounds(length):
    text = ("0123456789π🌈" * (length // 12 + 1))[:length]
    pages = EmbedStatics.paginate_chat_embed(text)
    assert "".join(page.description for page in pages) == text
    assert all(0 < len(page.description) <= 3500 for page in pages)
    assert all(len(page.description) <= 4096 for page in pages)
    if len(pages) == 1:
        assert pages[0].title == ""
    elif pages:
        assert [page.title for page in pages] == [
            str(index) for index in range(1, len(pages) + 1)
        ]
