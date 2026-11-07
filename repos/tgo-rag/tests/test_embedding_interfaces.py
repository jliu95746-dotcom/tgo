"""Verify the sync and async interfaces consumed by LangChain vector stores."""

import asyncio
import inspect
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from langchain_core.vectorstores import InMemoryVectorStore

from src.rag_service.services import embedding


@pytest.fixture(params=["openai", "openai_compatible", "qwen3"])
def configured_client(request, monkeypatch):
    calls = []

    def vectors(texts):
        calls.append((list(texts), threading.get_ident()))
        return [[float(len(text)), 1.0] for text in texts]

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.embeddings = SimpleNamespace(
                create=lambda **payload: SimpleNamespace(
                    data=[
                        SimpleNamespace(embedding=vector)
                        for vector in vectors(payload["input"])
                    ]
                )
            )

    class FakeLangChain:
        def __init__(self, **kwargs):
            pass

        def embed_query(self, text):
            return vectors([text])[0]

        def embed_documents(self, texts):
            return vectors(texts)

    monkeypatch.setattr(embedding, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(embedding, "OpenAIEmbeddings", FakeLangChain)
    service = embedding.EmbeddingService(
        provider=request.param,
        model="fixture-model",
        dimensions=2,
        batch_size=2,
        api_key="fixture-key",
        base_url=None
        if request.param == "openai"
        else "https://fixture.invalid/v1",
    )
    return service, calls


@pytest.mark.parametrize("batch", [False, True])
def test_sync_interfaces_return_vectors_not_coroutines(
    configured_client, batch
):
    service, calls = configured_client
    client = service.embeddings_client
    if batch:
        result = client.embed_documents(["a", "bb", "ccc"])
        expected = [[1.0, 1.0], [2.0, 1.0], [3.0, 1.0]]
    else:
        result = client.embed_query("query")
        expected = [5.0, 1.0]
    # Close a regressed coroutine so the failure does not leak a pending task.
    if inspect.iscoroutine(result):
        result.close()
    assert result == expected
    assert all(thread_id == threading.get_ident() for _, thread_id in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("batch", [False, True])
async def test_async_interfaces_execute_off_event_loop(
    configured_client, batch
):
    service, calls = configured_client
    client = service.embeddings_client
    if batch:
        result = await client.aembed_documents(["a", "bb", "ccc"])
        assert result == [[1.0, 1.0], [2.0, 1.0], [3.0, 1.0]]
    else:
        result = await client.aembed_query("query")
        assert result == [5.0, 1.0]
    assert calls
    assert all(thread_id != threading.get_ident() for _, thread_id in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("batch", [False, True])
async def test_service_uses_async_client_interfaces(configured_client, batch):
    service, _ = configured_client
    client = service.embeddings_client
    # The synchronous entry must never run directly in an async service call.
    if batch:
        original = client.embed_documents

        def sync_documents(texts):
            assert threading.get_ident() != event_thread
            return original(texts)

        client.embed_documents = sync_documents
        event_thread = threading.get_ident()
        assert await service.generate_embeddings_batch(["a", "bb", "ccc"]) == [
            [1.0, 1.0],
            [2.0, 1.0],
            [3.0, 1.0],
        ]
    else:
        original = client.embed_query

        def sync_query(text):
            assert threading.get_ident() != event_thread
            return original(text)

        client.embed_query = sync_query
        event_thread = threading.get_ident()
        assert await service.generate_embedding("query") == [5.0, 1.0]


@pytest.mark.asyncio
async def test_async_embedding_keeps_event_loop_responsive(monkeypatch):
    entered, release = threading.Event(), threading.Event()

    class BlockingClient:
        def embed_query(self, text):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("fixture release not received")
            return [0.25, 0.75]

    monkeypatch.setattr(
        embedding, "OpenAIEmbeddings", lambda **kw: BlockingClient()
    )
    client = embedding.OpenAIEmbeddingClient("fixture-key", "fixture", 2)
    task = asyncio.create_task(client.aembed_query("query"))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        # This executes while the provider call is still in flight.
        assert not task.done()
    finally:
        release.set()
    assert await asyncio.wait_for(task, 2) == [0.25, 0.75]


def test_standard_openai_passes_typed_api_key(monkeypatch):
    constructor = Mock()
    monkeypatch.setattr(embedding, "OpenAIEmbeddings", constructor)
    embedding.OpenAIEmbeddingClient("fixture-key", "fixture-model", 1536)
    arguments = constructor.call_args.kwargs
    assert arguments["api_key"].get_secret_value() == "fixture-key"
    assert arguments["model"] == "fixture-model"


def test_base_client_cannot_omit_sync_implementations():
    class Incomplete(embedding.BaseEmbeddingClient):
        def get_dimensions(self):
            return 2

        def get_model_name(self):
            return "fixture"

        async def aembed_documents(self, texts):
            return []

    with pytest.raises(TypeError):
        Incomplete()


def test_native_langchain_sync_ingestion_and_search(configured_client):
    service, _ = configured_client
    store = InMemoryVectorStore(service.embeddings_client)
    ids = store.add_texts(["a", "bb"], ids=["first", "second"])
    assert ids == ["first", "second"]
    result = store.similarity_search("bb", k=1)
    assert result[0].id == "second"


@pytest.mark.asyncio
async def test_native_langchain_async_ingestion_and_search(configured_client):
    service, _ = configured_client
    store = InMemoryVectorStore(service.embeddings_client)
    ids = await store.aadd_texts(["a", "bb"], ids=["first", "second"])
    assert ids == ["first", "second"]
    result = await store.asimilarity_search("bb", k=1)
    assert result[0].id == "second"


@pytest.mark.parametrize("batch", [False, True])
def test_missing_standard_client_is_rejected(monkeypatch, batch):
    monkeypatch.setattr(embedding, "OpenAIEmbeddings", lambda **kwargs: None)
    client = embedding.OpenAIEmbeddingClient("fixture-key", "fixture", 2)
    with pytest.raises(RuntimeError, match="not initialized"):
        if batch:
            client.embed_documents(["text"])
        else:
            client.embed_query("text")
