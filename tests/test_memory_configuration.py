from unittest.mock import Mock

import pytest

import gpt3discord


@pytest.fixture(autouse=True)
def no_memory_config(monkeypatch):
    for key in ("QDRANT_URL", "QDRANT_PATH", "QDRANT_API_KEY", "QDRANT_COLLECTION", "PINECONE_TOKEN"):
        monkeypatch.delenv(key, raising=False)


def test_default_memory_needs_no_optional_service(monkeypatch):
    loader = Mock(side_effect=AssertionError("Basic chat must not load a vector database"))
    monkeypatch.setattr(gpt3discord, "load_optional_service", loader)
    assert gpt3discord.load_conversation_memory() is None
    loader.assert_not_called()


@pytest.mark.parametrize("settings", [
    {"QDRANT_URL": "https://qdrant.example", "PINECONE_TOKEN": "test-key"},
    {"QDRANT_PATH": "./vectors", "PINECONE_TOKEN": "test-key"},
    {"QDRANT_API_KEY": "test-key", "PINECONE_TOKEN": "test-key"},
    {"QDRANT_URL": "https://qdrant.example", "QDRANT_PATH": "./vectors"},
    {"QDRANT_PATH": "./vectors", "QDRANT_API_KEY": "test-key"},
    {"QDRANT_API_KEY": "test-key"},
])
def test_ambiguous_memory_configuration_fails_before_loading(settings, monkeypatch):
    for key, value in settings.items():
        monkeypatch.setenv(key, value)
    loader = Mock()
    monkeypatch.setattr(gpt3discord, "load_optional_service", loader)
    with pytest.raises(ValueError):
        gpt3discord.load_conversation_memory()
    loader.assert_not_called()


@pytest.mark.parametrize("url,path,key", [
    ("https://qdrant.example", None, "test-key"),
    (None, "./vectors", None),
])
def test_explicit_qdrant_backend_configuration(url, path, key, monkeypatch):
    for name, value in (("QDRANT_URL", url), ("QDRANT_PATH", path), ("QDRANT_API_KEY", key)):
        if value is not None:
            monkeypatch.setenv(name, value)
    monkeypatch.setenv("QDRANT_COLLECTION", "my-memory")
    backend = Mock()
    loader = Mock(return_value=backend)
    monkeypatch.setattr(gpt3discord, "load_optional_service", loader)
    assert gpt3discord.load_conversation_memory() is backend.from_config.return_value
    loader.assert_called_once_with("services.qdrant_service", "QdrantService", "qdrant")
    backend.from_config.assert_called_once_with(url=url, path=path, api_key=key, collection_name="my-memory")


def test_existing_pinecone_configuration_is_preserved(monkeypatch):
    monkeypatch.setenv("PINECONE_TOKEN", "test-pinecone-key")
    monkeypatch.setenv("PINECONE_REGION", "eu-west-1")
    backend = Mock()
    loader = Mock(return_value=backend)
    monkeypatch.setattr(gpt3discord, "load_optional_service", loader)
    assert gpt3discord.load_conversation_memory() is backend.from_config.return_value
    loader.assert_called_once_with("services.pinecone_service", "PineconeService", "pinecone")
    backend.from_config.assert_called_once_with("test-pinecone-key", "eu-west-1")
