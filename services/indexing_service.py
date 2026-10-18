"""Per-request LlamaIndex configuration shared by optional data and search tools."""

from dataclasses import dataclass
import asyncio
import json
from pathlib import Path
import tempfile
from urllib.parse import urljoin, urlparse

import aiohttp
import requests
from bs4 import BeautifulSoup
from llama_index.core import Document, SimpleDirectoryReader, set_global_tokenizer
from llama_index.core.callbacks import CallbackManager, TokenCountingHandler
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.indices.prompt_helper import PromptHelper, ChatPromptHelper
from llama_index.core.utilities.token_counting import TokenCounter
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.llms.openai import OpenAIResponses

from models.openai_model import Models
from services.environment_service import EnvService
from services.openai_client_service import client_routing, RequestOpenAI, RequestAsyncOpenAI
from services.request_context import single_attempt_requests
from services.usage_service import _load_tokenizer


class ByteTokenizer:
    """Conservative local estimates when the vocabulary cannot be downloaded."""

    def encode(self, text, **kwargs):
        return list(text.encode("utf-8"))

    def decode(self, tokens):
        return bytes(tokens).decode("utf-8", errors="replace")


def index_tokenizer():
    return _load_tokenizer() or ByteTokenizer()


class ByteTokenCounter(TokenCounter):
    def estimate_tokens_in_messages(self, messages):
        # LlamaIndex's default counter neglects its tokenizer when counting
        # content blocks. Count the serialized messages conservatively instead.
        return sum(
            self.get_string_tokens(message.model_dump_json()) + 3
            for message in messages
        )

    async def aestimate_tokens_in_messages(self, messages):
        return self.estimate_tokens_in_messages(messages)


def uses_byte_tokenizer(tokenizer):
    return isinstance(getattr(tokenizer, "__self__", None), ByteTokenizer)


class IndexEmbedding(OpenAIEmbedding):
    def __init__(self, **kwargs):
        # The SDK owns bounded retries; a second LlamaIndex retry loop would
        # repeat a claimed recovery request even after the SDK stops.
        kwargs.setdefault("max_retries", 0)
        super().__init__(**kwargs)

    def _get_client(self):
        return super()._get_client().with_options(
            max_retries=0 if single_attempt_requests.get() else 2
        )

    def _get_aclient(self):
        return super()._get_aclient().with_options(
            max_retries=0 if single_attempt_requests.get() else 2
        )

    def _get_credential_kwargs(self, is_async=False):
        return {
            **super()._get_credential_kwargs(is_async),
            **client_routing(self.api_key),
        }


class IndexResponses(OpenAIResponses):
    def __init__(self, **kwargs):
        key = kwargs.get("api_key") or EnvService.get_openai_token()
        kwargs["api_key"] = key
        kwargs.setdefault("api_base", EnvService.get_openai_base_url())
        kwargs.setdefault("max_retries", 0)
        clients = {
            "api_key": key,
            "base_url": kwargs["api_base"],
            "timeout": kwargs.get("timeout", 60.0),
            **client_routing(key),
        }
        if kwargs.get("openai_client") is None:
            kwargs["openai_client"] = RequestOpenAI(
                **clients, http_client=kwargs.get("http_client")
            )
        if kwargs.get("async_openai_client") is None:
            kwargs["async_openai_client"] = RequestAsyncOpenAI(
                **clients, http_client=kwargs.get("async_http_client")
            )
        super().__init__(**kwargs)

    def _get_credential_kwargs(self, is_async=False):
        return {
            **super()._get_credential_kwargs(is_async),
            **client_routing(self.api_key),
        }

    @property
    def _tokenizer(self):
        return index_tokenizer()


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
            "transformations": [
                SentenceSplitter(
                    chunk_size=1024,
                    chunk_overlap=20,
                    tokenizer=self.token_counter.tokenizer,
                )
            ],
        }

    @property
    def query_kwargs(self):
        tokenizer = self.token_counter.tokenizer
        prompt_helper = PromptHelper.from_llm_metadata(
            self.llm.metadata, tokenizer=tokenizer
        )
        chat_prompt_helper = ChatPromptHelper.from_llm_metadata(
            self.llm.metadata, tokenizer=tokenizer
        )
        if uses_byte_tokenizer(tokenizer):
            prompt_helper._token_counter = ByteTokenCounter(tokenizer=tokenizer)
            chat_prompt_helper._token_counter = ByteTokenCounter(tokenizer=tokenizer)
        return {
            "llm": self.llm,
            "callback_manager": self.callback_manager,
            "prompt_helper": prompt_helper,
            "chat_prompt_helper": chat_prompt_helper,
        }


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
    Models.validate_text_model(model)
    embedding_model = embedding_model or EnvService.get_openai_embedding_model()
    embed_kwargs = (
        {} if embedding_model == "text-embedding-ada-002" else {"dimensions": 1536}
    )
    tokenizer = index_tokenizer().encode
    # Tree summaries and tool routers resolve a process-wide tokenizer even
    # with per-index callbacks. This counter contains no account state.
    set_global_tokenizer(tokenizer)
    token_counter = TokenCountingHandler(tokenizer=tokenizer)
    if uses_byte_tokenizer(tokenizer):
        token_counter._token_counter = ByteTokenCounter(tokenizer=tokenizer)
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


async def read_pdf_documents(url):
    """Read a bounded PDF download and reject unreadable or textless documents."""
    max_bytes = 25 * 1024 * 1024
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "document.pdf"
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30)
        ) as session:
            async with session.get(
                url, proxy=EnvService.get_http_proxy(url)
            ) as response:
                response.raise_for_status()
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > max_bytes:
                    raise ValueError("PDF files must be 25 MiB or smaller.")
                downloaded = 0
                with path.open("wb") as destination:
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        downloaded += len(chunk)
                        if downloaded > max_bytes:
                            raise ValueError("PDF files must be 25 MiB or smaller.")
                        destination.write(chunk)
        with path.open("rb") as source:
            if b"%PDF-" not in source.read(1024):
                raise ValueError("The URL did not return a valid PDF file.")
        try:
            documents = await asyncio.to_thread(
                lambda: SimpleDirectoryReader(
                    input_files=[str(path)], raise_on_error=True
                ).load_data()
            )
        except ImportError:
            raise
        except Exception:
            raise ValueError(
                "Invalid PDF. Upload a valid, unlocked file or export it as text."
            ) from None
        if not any(document.get_content().strip() for document in documents):
            raise ValueError(
                "The PDF contains no extractable text. Run OCR on scanned pages or upload a text export."
            )
    for document in documents:
        document.metadata["URL"] = url
    return documents


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
