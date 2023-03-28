from pathlib import Path

import pytest
from models.openai_model import Model
from transformers import GPT2TokenizerFast

from services.usage_service import UsageService


# Non-ChatGPT -> TODO: make generic test and loop through text models
@pytest.mark.asyncio
async def test_send_req():
    usage_service = UsageService(Path("../tests"))
    model = Model(usage_service)
    prompt = "how many hours are in a day?"
    tokens = len(GPT2TokenizerFast.from_pretrained("gpt2")(prompt)["input_ids"])
    res = await model.send_request(prompt, tokens)
    assert "24" in res["choices"][0]["text"]


# ChatGPT version
@pytest.mark.asyncio
async def test_send_req_gpt():
    usage_service = UsageService(Path("../tests"))
    model = Model(usage_service)
    prompt = "how many hours are in a day?"
    res = await model.send_request(
        prompt, None, is_chatgpt_request=True, model="gpt-3.5-turbo"
    )
    assert "24" in res["choices"][0]["message"]["content"]


# GPT4 version
@pytest.mark.asyncio
async def test_send_req_gpt4():
    usage_service = UsageService(Path("../tests"))
    model = Model(usage_service)
    prompt = "how many hours are in a day?"
    res = await model.send_request(prompt, None, is_chatgpt_request=True, model="gpt-4")
    assert "24" in res["choices"][0]["message"]["content"]


# Edit request -> currently broken due to endpoint
# @pytest.mark.asyncio
# async def test_send_edit_req():
#     usage_service = UsageService(Path("../tests"))
#     model = Model(usage_service)
#     text = 'how many hours are in a day?'
#     res = await model.send_edit_request(text)
#     assert '24' in res['choices'][0]['text']
