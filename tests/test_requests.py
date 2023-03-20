from pathlib import Path

import pytest
from models.openai_model import Models, Model
from transformers import GPT2TokenizerFast
import asyncio

from services.usage_service import UsageService
import os

# Non-ChatGPT
@pytest.mark.asyncio
async def test_send_req():

    usage_service = UsageService(Path("../tests"))
    model = Model(usage_service)
    prompt = 'how many hours are in a day?'
    tokens = len(GPT2TokenizerFast.from_pretrained("gpt2")(prompt)["input_ids"])
    # tokens = 60
    res = await model.send_request(prompt, tokens)
    assert '24' in res['choices'][0]['text']
