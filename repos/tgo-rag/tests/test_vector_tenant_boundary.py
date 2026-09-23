"""Tenant isolation at the shared vector-index boundary."""

import os
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_postgres.v2.async_vectorstore import AsyncPGVectorStore
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.rag_service.services.vector_store import VectorStoreService


FILTER_CASES = [
    pytest.param(None, ["own"], id="missing-filter"),
    pytest.param({}, ["own"], id="empty-filter"),
    pytest.param({"collection_id": "shared"}, ["own"], id="collection"),
    pytest.param({"project_id": "company-b"}, [], id="foreign-project"),
    pytest.param(
        {"$or": [{"project_id": "company-b"}, {"collection_id": "shared"}]},
        ["own"],
        id="nested-or",
    ),
    pytest.param(
        {"$not": {"project_id": "company-a"}}, [], id="negated-project"
    ),
]


async def capture_filter(monkeypatch, filters):
    """Run the production helper without a model or vector-index write."""
    vector_store = SimpleNamespace(
        similarity_search_with_score=Mock(return_value=[])
    )
    service = VectorStoreService()
    monkeypatch.setattr(
        service,
        "get_vector_store_for_project",
        AsyncMock(return_value=vector_store),
    )
    before = deepcopy(filters)
    await service.similarity_search_for_project(
        query="synthetic",
        project_key="company-a",
        embeddings_client=SimpleNamespace(),
        filter_dict=filters,
    )
    assert filters == before, "Caller-owned filters were mutated"
    return vector_store.similarity_search_with_score.call_args.kwargs["filter"]


@pytest.mark.asyncio
@pytest.mark.parametrize("filters,expected_ids", FILTER_CASES)
async def test_project_filter_is_mandatory(monkeypatch, filters, expected_ids):
    effective = await capture_filter(monkeypatch, filters)
    expected = {"project_id": "company-a"}
    if filters:
        expected = {"$and": [expected, filters]}
    assert effective == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("project_key", ["", " ", "\t"])
async def test_empty_project_rejected_before_embedding_setup(
    monkeypatch, project_key
):
    service = VectorStoreService()
    lookup = AsyncMock()
    monkeypatch.setattr(service, "get_vector_store_for_project", lookup)
    with pytest.raises(ValueError, match="project_key"):
        await service.similarity_search_for_project(
            query="synthetic",
            project_key=project_key,
            embeddings_client=SimpleNamespace(),
        )
    lookup.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("SAAS_TEST_DATABASE_URL"),
    reason="Opt-in PostgreSQL isolation test",
)
@pytest.mark.parametrize("filters,expected_ids", FILTER_CASES)
async def test_real_filter_sql_cannot_match_other_company(
    monkeypatch, filters, expected_ids
):
    effective = await capture_filter(monkeypatch, filters)
    # Compile with the installed vector library, not a test reimplementation.
    compiler = object.__new__(AsyncPGVectorStore)
    clause, parameters = compiler._create_filter_clause(effective)
    statement = text(
        "WITH candidates(id, project_id, collection_id) AS (VALUES "
        "('own', 'company-a', 'shared'), "
        "('foreign', 'company-b', 'shared')) "
        f"SELECT id FROM candidates WHERE {clause} ORDER BY id"
    )
    engine = create_async_engine(os.environ["SAAS_TEST_DATABASE_URL"])
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            result = await connection.execute(statement, parameters)
            assert result.scalars().all() == expected_ids
            await connection.rollback()
    finally:
        await engine.dispose()
