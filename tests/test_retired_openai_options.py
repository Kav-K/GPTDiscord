"""Retired options fail locally while supported APIs and saved state remain usable."""

import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from models import openai_model
from models.openai_model import ImageSize, Model, Models
from services.responses_service import ResponsesService
from tests.test_openai_model import completion, fake_api, model  # noqa: F401
from utils.model_settings import editable_model_settings


RETIRED_EXAMPLES = [
    "text-davinci-003", "gpt-3.5-turbo-0613", "gpt-4-vision-preview",
    "gpt-4-turbo-preview", "o1-mini-2024-09-12", "gpt-4.5-preview-2025-02-27",
    "chatgpt-4o-latest", "gpt-5-chat-latest", "gpt-5.4-cyber",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("selected", RETIRED_EXAMPLES)
async def test_retired_typed_model_fails_before_chat_request(model, fake_api, selected):
    with pytest.raises(ValueError, match="retired"):
        await model.send_request("Hello", None, model=selected)
    assert fake_api.calls == []


@pytest.mark.asyncio
async def test_retired_conversation_model_fails_before_chat_request(model, fake_api):
    with pytest.raises(ValueError, match="retired"):
        await model.send_chatgpt_chat_request([], "gpt-4-32k", "Bot", "User")
    assert fake_api.calls == []


@pytest.mark.asyncio
async def test_retired_configured_default_fails_before_key_validation(model, fake_api, monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4-vision-preview")
    with pytest.raises(ValueError, match="retired"):
        await Model.send_test_request("offline-user-key")
    with pytest.raises(ValueError, match="retired"):
        Model(model.usage_service)
    assert fake_api.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("selected", [
    "gpt-3.5-turbo", "gpt-4", "gpt-4-1106-preview", "gpt-4-turbo",
    "gpt-4.1-nano", "o1", "o3-mini", "o4-mini", "gpt-4o", "gpt-4.1",
    "gpt-5.1", "gpt-5.4-nano", "custom-deployment",
])
async def test_upcoming_shutdowns_and_custom_models_remain_requestable(model, fake_api, selected):
    fake_api.queued.append((completion(), 200))
    await model.send_request("Hello", None, model=selected)
    assert fake_api.calls[0][1]["json"]["model"] == selected


@pytest.mark.asyncio
async def test_optional_agents_reject_retired_model_before_sdk_construction(monkeypatch):
    pytest.importorskip("langchain_openai")
    pytest.importorskip("langchain_classic")
    pytest.importorskip("langchain_community")
    agent = importlib.import_module("services.agent_service")
    sync, asynchronous = Mock(), Mock()
    monkeypatch.setattr(agent, "_AgentOpenAI", sync)
    monkeypatch.setattr(agent, "_AgentAsyncOpenAI", asynchronous)
    with pytest.raises(ValueError, match="retired"):
        agent.create_chat_llm("o1-mini")
    sync.assert_not_called()
    asynchronous.assert_not_called()


def test_index_rejects_retired_model_before_sdk_construction(monkeypatch):
    pytest.importorskip("llama_index.core")
    pytest.importorskip("llama_index.llms.openai")
    pytest.importorskip("llama_index.embeddings.openai")
    indexing = importlib.import_module("services.indexing_service")
    llm, embedding = Mock(), Mock()
    monkeypatch.setattr(indexing, "IndexResponses", llm)
    monkeypatch.setattr(indexing, "IndexEmbedding", embedding)
    with pytest.raises(ValueError, match="retired"):
        indexing.create_index_context(api_key="offline-user-key", model="gpt-4-turbo-preview")
    llm.assert_not_called()
    embedding.assert_not_called()


@pytest.mark.asyncio
async def test_native_web_rejects_retired_model_before_sdk_request():
    create = AsyncMock()
    with pytest.raises(ValueError, match="retired"):
        await ResponsesService(SimpleNamespace(responses=SimpleNamespace(create=create))).search(
            "Search for current news", "gpt-4-turbo-preview"
        )
    create.assert_not_awaited()


def test_standalone_web_validation_has_no_model_database_side_effects(tmp_path):
    code = '''
import asyncio, json, sys
from types import SimpleNamespace
from services.responses_service import ResponsesService
calls=[]
async def create(**kwargs):
    calls.append(kwargs["model"])
    return {"output":[],"status":"completed"}
async def run():
    service=ResponsesService(SimpleNamespace(responses=SimpleNamespace(create=create)))
    for selected in ("gpt-6.1-sol","custom-deployment"):
        await service.search("Current news",selected)
    try:
        await service.search("Current news","gpt-4-vision-preview")
    except ValueError:
        pass
    else:
        raise AssertionError("Retired model was accepted")
asyncio.run(run())
assert "models.openai_model" not in sys.modules
print(json.dumps(calls))
'''
    environment = dict(os.environ)
    environment.update(DATA_DIR=str(tmp_path), SHARE_DIR=str(tmp_path), PYTHON_DOTENV_DISABLED="1")
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    result = subprocess.run(
        [sys.executable, "-B", "-c", code], cwd=tmp_path, env=environment,
        capture_output=True, text=True, timeout=15, check=True,
    )
    assert json.loads(result.stdout.splitlines()[-1]) == ["gpt-6.1-sol", "custom-deployment"]
    assert list(tmp_path.iterdir()) == []


def test_retired_settings_are_neither_read_nor_written_during_startup(model, monkeypatch):
    retired = {"best_of", "mode", "use_org"}
    touched = []

    class OldSettings(dict):
        def __contains__(self, key):
            if key in retired:
                touched.append(key)
                raise AssertionError("Retired setting inspected")
            return super().__contains__(key)

        def __getitem__(self, key):
            if key in retired:
                touched.append(key)
                raise AssertionError("Retired setting read")
            return super().__getitem__(key)

        def get(self, key, default=None):
            if key in retired:
                touched.append(key)
                raise AssertionError("Retired setting read")
            return super().get(key, default)

        def __setitem__(self, key, value):
            if key in retired:
                touched.append(key)
                raise AssertionError("Retired setting written")
            super().__setitem__(key, value)

        def __delitem__(self, key):
            if key in retired:
                touched.append(key)
                raise AssertionError("Retired setting removed")
            super().__delitem__(key)

    saved = OldSettings({key: "invalid obsolete value" for key in retired})
    monkeypatch.setattr(openai_model, "SETTINGS_DB", saved)
    instance = Model(model.usage_service)

    assert touched == []
    assert retired.isdisjoint(editable_model_settings(Model))
    assert all(not hasattr(instance, key) for key in retired)
    assert {key: value for key, value in saved.items() if key in retired} == {
        key: "invalid obsolete value" for key in retired
    }


def test_explicit_reset_cleans_retired_settings_and_preserves_unrelated_rows(model, monkeypatch):
    retired = {"best_of", "mode", "use_org"}
    saved = {key: "invalid obsolete value" for key in retired}
    saved["unrelated"] = "preserve"
    monkeypatch.setattr(openai_model, "SETTINGS_DB", saved)
    instance = Model(model.usage_service)

    instance.reset_settings()

    assert retired.isdisjoint(saved)
    assert saved["unrelated"] == "preserve"


@pytest.mark.parametrize("saved", ["256x256", "512x512"])
def test_saved_legacy_image_size_is_normalized_without_rewriting_storage(model, monkeypatch, saved):
    database = {"image_size": saved}
    monkeypatch.setattr(openai_model, "SETTINGS_DB", database)
    instance = Model(model.usage_service)
    assert instance.image_size == "1024x1024"
    assert database["image_size"] == saved


def test_new_image_size_default_is_square(model):
    assert model.image_size == ImageSize.SQUARE == "1024x1024"
    assert openai_model.SETTINGS_DB["image_size"] == ImageSize.SQUARE


@pytest.mark.parametrize("selected", ImageSize.ALL_SIZES)
def test_current_image_size_selections_are_saved(model, selected):
    model.image_size = selected
    assert model.image_size == openai_model.SETTINGS_DB["image_size"] == selected


@pytest.mark.parametrize("selected", ["256x256", "512x512", "1792x1024", "1024x1792"])
def test_legacy_image_sizes_cannot_be_selected_again(model, selected):
    before = model.image_size
    with pytest.raises(ValueError, match="Image size"):
        model.image_size = selected
    assert model.image_size == openai_model.SETTINGS_DB["image_size"] == before
