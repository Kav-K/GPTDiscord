"""Per-request LlamaIndex configuration shared by optional data and search tools."""

from dataclasses import dataclass
import json
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import tiktoken
from bs4 import BeautifulSoup
from llama_index.core import Document
from llama_index.core.callbacks import CallbackManager, TokenCountingHandler
from llama_index.core.node_parser import SentenceSplitter
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.llms.openai import OpenAIResponses

from models.openai_model import Models
from services.environment_service import EnvService
from services.openai_client_service import client_routing


class IndexEmbedding(OpenAIEmbedding):
    def _get_credential_kwargs(self, is_async=False):
        return {
            **super()._get_credential_kwargs(is_async),
            **client_routing(self.api_key),
        }


class IndexResponses(OpenAIResponses):
    def _get_credential_kwargs(self, is_async=False):
        return {
            **super()._get_credential_kwargs(is_async),
            **client_routing(self.api_key),
        }

    @property
    def _tokenizer(self):
        try:
            return tiktoken.encoding_for_model(self.model)
        except KeyError:
            return tiktoken.get_encoding("o200k_base")


@dataclass
class IndexContext:
    llm: object
    embed_model: object
    callback_manager: CallbackManager
    token_counter: TokenCountingHandler

    @property
    def index_kwargs(self):
        return {
            "llm": self.llm,
            "embed_model": self.embed_model,
            "callback_manager": self.callback_manager,
            "transformations": [SentenceSplitter(chunk_size=1024, chunk_overlap=20)],
        }

    @property
    def query_kwargs(self):
        return {"llm": self.llm, "callback_manager": self.callback_manager}


def create_index_context(api_key=None, model=None, llm=None, embedding_model=None):
    """Create clients without changing environment variables or global Settings."""
    key = api_key or EnvService.get_openai_token()
    model = model or EnvService.get_openai_model() or Models.DEFAULT
    kwargs = {}
    if llm is not None:
        # Data chats use a LangChain client. Give query synthesis its own native
        # LlamaIndex client with the same account/model instead of LLMPredictor.
        model = llm.model_name
        llm_key = getattr(llm, "openai_api_key", None)
        if llm_key is not None:
            key = llm_key.get_secret_value()
        kwargs["temperature"] = llm.temperature or 0
    if Models.is_reasoning(model):
        kwargs.pop("temperature", None)
        kwargs["reasoning_options"] = {}
    embedding_model = embedding_model or EnvService.get_openai_embedding_model()
    embed_kwargs = (
        {} if embedding_model == "text-embedding-ada-002" else {"dimensions": 1536}
    )
    token_counter = TokenCountingHandler(
        tokenizer=tiktoken.get_encoding("cl100k_base").encode
    )
    callbacks = CallbackManager([token_counter])
    return IndexContext(
        llm=IndexResponses(
            model=model,
            api_key=key,
            api_base=EnvService.get_openai_base_url(),
            context_window=Models.get_max_tokens(model),
            max_output_tokens=min(4096, Models.get_max_output_tokens(model)),
            callback_manager=callbacks,
            **kwargs,
        ),
        embed_model=IndexEmbedding(
            model=embedding_model,
            api_key=key,
            api_base=EnvService.get_openai_base_url(),
            callback_manager=callbacks,
            **embed_kwargs,
        ),
        callback_manager=callbacks,
        token_counter=token_counter,
    )


def read_web_documents(url):
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()
    return [
        Document(text=soup.get_text(" ", strip=True), metadata={"URL": response.url})
    ]


class RecursiveWebReader:
    """Read a bounded set of pages on the supplied site's hostname."""

    def __init__(self, depth=1):
        self.depth = max(0, min(int(depth), 5))

    def load_data(self, urls):
        documents = []
        pending = [(url, 0) for url in urls]
        seen = set()
        while pending and len(seen) < 25:
            url, depth = pending.pop(0)
            if url in seen:
                continue
            seen.add(url)
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            if depth < self.depth:
                for link in soup.find_all("a", href=True):
                    target = urljoin(response.url, link["href"]).split("#")[0]
                    if (
                        urlparse(target).scheme in ("https", "http")
                        and urlparse(target).netloc == urlparse(response.url).netloc
                    ):
                        pending.append((target, depth + 1))
            for tag in soup(["script", "style", "nav", "footer"]):
                tag.decompose()
            documents.append(
                Document(
                    text=soup.get_text(" ", strip=True), metadata={"URL": response.url}
                )
            )
        return documents


def persist_index(index, persist_dir):
    """Persist the embedding model alongside vectors for compatible later queries."""
    index.storage_context.persist(persist_dir=persist_dir)
    embed_model = getattr(index, "_embed_model", None)
    model = getattr(embed_model, "model_name", None)
    if model:
        (Path(persist_dir) / "gptdiscord-index.json").write_text(
            json.dumps({"embedding_model": model}), encoding="utf-8"
        )


def context_for_stored_index(persist_dir, context):
    metadata_path = Path(persist_dir) / "gptdiscord-index.json"
    model = "text-embedding-ada-002"
    if metadata_path.exists():
        model = json.loads(metadata_path.read_text(encoding="utf-8"))["embedding_model"]
    return create_index_context(
        api_key=context.embed_model.api_key,
        model=context.llm.model,
        embedding_model=model,
    )


def embedding_for_index(index, context):
    """Use the stored vector space with the current request's credentials."""
    stored = getattr(index, "_embed_model", None)
    if not isinstance(stored, OpenAIEmbedding):
        return stored or context.embed_model
    model = stored.model_name
    if model == context.embed_model.model_name:
        return context.embed_model
    kwargs = {} if model == "text-embedding-ada-002" else {"dimensions": 1536}
    return IndexEmbedding(
        model=model,
        api_key=context.embed_model.api_key,
        api_base=EnvService.get_openai_base_url(),
        callback_manager=context.callback_manager,
        **kwargs,
    )
