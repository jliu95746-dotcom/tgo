"""Regression tests for pure vector similarity search."""

from types import SimpleNamespace

import pytest
from langchain_core.documents import Document

from src.rag_service.services.vector_store import VectorStoreService


class FakeVectorStore:
    """Synchronous stand-in for langchain-postgres' vector store."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def similarity_search_with_score(self, **kwargs):
        self.calls.append(kwargs)
        return [
            (Document(page_content="less relevant"), 0.8),
            (Document(page_content="more relevant"), 0.2),
        ]


@pytest.mark.asyncio
async def test_similarity_search_disables_hybrid_fusion(monkeypatch) -> None:
    fake_store = FakeVectorStore()
    service = VectorStoreService()

    async def get_vector_store():
        return fake_store

    monkeypatch.setattr(service, "get_vector_store", get_vector_store)

    results = await service.similarity_search("女包推荐", k=2)

    assert fake_store.calls[0]["hybrid_search_config"] is None
    assert [document.page_content for document, _score in results] == [
        "more relevant",
        "less relevant",
    ]
    assert [score for _document, score in results] == pytest.approx([0.8, 0.2])


@pytest.mark.asyncio
async def test_project_similarity_search_disables_hybrid_fusion(monkeypatch) -> None:
    fake_store = FakeVectorStore()
    service = VectorStoreService()

    async def get_vector_store_for_project(project_key, embeddings_client):
        assert project_key == "project-1"
        assert embeddings_client is not None
        return fake_store

    monkeypatch.setattr(
        service,
        "get_vector_store_for_project",
        get_vector_store_for_project,
    )

    results = await service.similarity_search_for_project(
        query="女包推荐",
        project_key="project-1",
        embeddings_client=SimpleNamespace(),
        k=2,
    )

    assert fake_store.calls[0]["hybrid_search_config"] is None
    assert [document.page_content for document, _score in results] == [
        "more relevant",
        "less relevant",
    ]
    assert [score for _document, score in results] == pytest.approx([0.8, 0.2])
