from pathlib import Path
import tempfile

import pytest
from models.openai_model import Model

from services.usage_service import UsageService


@pytest.fixture
def usage_service(tmp_path):
    return UsageService(tmp_path)


@pytest.fixture
def model(usage_service):
    return Model(usage_service)


# All requests now use chat completions format
@pytest.mark.asyncio
async def test_send_req(model):
    prompt = "how many hours are in a day?"
    res = await model.send_request(prompt, None, model="gpt-4o-mini")
    assert "24" in res["choices"][0]["message"]["content"]


# ChatGPT version
@pytest.mark.asyncio
async def test_send_req_gpt(model):
    prompt = "how many hours are in a day?"
    res = await model.send_request(
        prompt, None, is_chatgpt_request=True, model="gpt-4o-mini"
    )
    assert "24" in res["choices"][0]["message"]["content"]


# GPT4 version
@pytest.mark.asyncio
async def test_send_req_gpt4(model):
    prompt = "how many hours are in a day?"
    res = await model.send_request(
        prompt, None, is_chatgpt_request=True, model="gpt-4o"
    )
    assert "24" in res["choices"][0]["message"]["content"]


# Edit request - now uses chat completions
@pytest.mark.asyncio
async def test_send_edit_req(model):
    instruction = "Fix the spelling"
    text = "Ther are tweny four hours in a day"
    res = await model.send_edit_request(instruction=instruction, text=text)
    assert "choices" in res
