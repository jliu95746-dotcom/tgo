"""Real SDK HTTP contracts and an opt-in owned-PostgreSQL round trip."""

import json
import os
import threading
from contextlib import contextmanager
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_openai import OpenAIEmbeddings
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from src.rag_service.models import Base
from src.rag_service.services import embedding, vector_store


def fixture_vector(value):
    return [1.0, float(len(value))] + [0.0] * 1534


@pytest.fixture
def embedding_server():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            size = int(self.headers["Content-Length"])
            payload = json.loads(self.rfile.read(size))
            assert self.path == "/v1/embeddings"
            assert self.headers["Authorization"] == "Bearer fixture-key"
            values = payload["input"]
            if isinstance(values, str):
                values = [values]
            requests.append(payload)
            body = json.dumps(
                {
                    "object": "list",
                    "model": payload["model"],
                    "data": [
                        {
                            "object": "embedding",
                            "index": index,
                            "embedding": fixture_vector(value),
                        }
                        for index, value in enumerate(values)
                    ],
                    "usage": {"prompt_tokens": 1, "total_tokens": 1},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield SimpleNamespace(
            url=f"http://127.0.0.1:{server.server_port}/v1", requests=requests
        )
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        assert not worker.is_alive()


@contextmanager
def sdk_client(provider, server, monkeypatch):
    if provider == "openai":
        # Preserve the real LangChain implementation without tokenizer I/O.
        monkeypatch.setattr(
            embedding,
            "OpenAIEmbeddings",
            partial(
                OpenAIEmbeddings,
                base_url=server.url,
                check_embedding_ctx_length=False,
            ),
        )
        client = embedding.OpenAIEmbeddingClient(
            "fixture-key", "fixture", 1536
        )
    elif provider == "qwen3":
        client = embedding.Qwen3EmbeddingClient(
            "fixture-key", server.url, "fixture", 1536, batch_size=2
        )
    else:
        client = embedding.OpenAIEmbeddingClient(
            "fixture-key", "fixture", 1536, batch_size=2, base_url=server.url
        )
    try:
        yield client
    finally:
        if isinstance(client, embedding.Qwen3EmbeddingClient):
            client._client.close()
        elif client._compat_client is not None:
            client._compat_client.close()
        elif client._client is not None:
            client._client.client._client.close()


@pytest.mark.parametrize("provider", ["openai", "openai_compatible", "qwen3"])
def test_native_sdk_sync_requests(provider, embedding_server, monkeypatch):
    with sdk_client(provider, embedding_server, monkeypatch) as client:
        assert client.embed_query("query") == fixture_vector("query")
        assert client.embed_documents(["a", "bb", "ccc"]) == [
            fixture_vector(value) for value in ["a", "bb", "ccc"]
        ]
    assert embedding_server.requests
    assert all(
        call["model"] == "fixture" for call in embedding_server.requests
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "openai_compatible", "qwen3"])
async def test_native_sdk_async_requests(
    provider, embedding_server, monkeypatch
):
    with sdk_client(provider, embedding_server, monkeypatch) as client:
        assert await client.aembed_query("query") == fixture_vector("query")
        assert await client.aembed_documents(["a", "bb", "ccc"]) == [
            fixture_vector(value) for value in ["a", "bb", "ccc"]
        ]


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("TGO_EMBEDDING_TEST_DATABASE_URL"),
    reason="Requires an explicitly owned local embedding test database",
)
async def test_real_pgvector_write_query_and_tenant_isolation(
    embedding_server, monkeypatch
):
    url = os.environ["TGO_EMBEDDING_TEST_DATABASE_URL"]
    parsed = make_url(url)
    assert parsed.host == "127.0.0.1"
    assert parsed.database and parsed.database.startswith(
        "tgo_embedding_test_"
    )
    db = create_async_engine(url)
    service = None
    initialized = False
    project_a, project_b = uuid4(), uuid4()
    ids = [str(uuid4()), str(uuid4())]
    try:
        # Only this test's dedicated database is initialized; no existing data.
        async with db.begin() as connection:
            existing = await connection.scalar(
                text(
                    "SELECT count(*) FROM pg_tables "
                    "WHERE schemaname = 'public'"
                )
            )
            assert (
                existing == 0
            ), "The explicitly owned test database must be empty"
            initialized = True
            await connection.execute(
                text("CREATE EXTENSION IF NOT EXISTS vector")
            )
            await connection.run_sync(Base.metadata.create_all)
        with sdk_client(
            "openai_compatible", embedding_server, monkeypatch
        ) as client:
            monkeypatch.setattr(
                vector_store,
                "get_embedding_service",
                lambda: SimpleNamespace(embeddings_client=client),
            )
            service = vector_store.VectorStoreService()
            service.settings = SimpleNamespace(
                database_url=url, embedding_batch_size=2
            )
            await service.get_vector_store()
            store_a = await service.get_vector_store_for_project(
                str(project_a), client
            )
            store_b = await service.get_vector_store_for_project(
                str(project_b), client
            )
            assert (
                await store_a.aadd_texts(
                    ["a"],
                    ids=ids[:1],
                    metadatas=[{"project_id": str(project_a)}],
                )
                == ids[:1]
            )
            assert (
                await store_b.aadd_texts(
                    ["bb"],
                    ids=ids[1:],
                    metadatas=[{"project_id": str(project_b)}],
                )
                == ids[1:]
            )
            results = await service.similarity_search_for_project(
                query="a",
                project_key=str(project_a),
                embeddings_client=client,
                k=5,
            )
            assert [document.id for document, _ in results] == ids[:1]
            assert results[0][0].metadata["project_id"] == project_a
            assert results[0][1] == pytest.approx(1.0)
            assert (
                await service.similarity_search_for_project(
                    query="bb",
                    project_key=str(project_a),
                    embeddings_client=client,
                    filter_dict={"project_id": str(project_b)},
                )
                == []
            )
            assert await store_a.adelete(ids=ids[:1])
            assert await store_b.adelete(ids=ids[1:])
    finally:
        if service is not None and service._pg_engine is not None:
            await service._pg_engine.close()
        if initialized:
            async with db.begin() as connection:
                await connection.run_sync(Base.metadata.drop_all)
        await db.dispose()
