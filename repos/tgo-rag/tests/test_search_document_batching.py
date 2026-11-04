"""Semantic hydration must not open a database session for every vector hit."""
from contextlib import asynccontextmanager
from datetime import UTC, datetime
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.rag_service.models import (
    Collection, CollectionType, File, FileDocument,
)
from src.rag_service.services import search as search_module
from src.rag_service.services.search import SearchService


def document(project_id, collection_id, file_id):
    return FileDocument(
        id=uuid4(), project_id=project_id, collection_id=collection_id,
        file_id=file_id, content="已审核商品资料", content_type="paragraph",
        created_at=datetime(2026, 11, 4, tzinfo=UTC),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("hit_count", [1, 24])
async def test_semantic_hits_share_one_metadata_read(monkeypatch, hit_count):
    project_id, collection_id, file_id = uuid4(), uuid4(), uuid4()
    documents = [document(project_id, collection_id, file_id)
                 for _ in range(hit_count)]
    source = File(id=file_id, project_id=project_id,
                  original_filename="商品资料.md", file_size=500)
    statements = []

    class Session:
        async def execute(self, statement):
            statements.append(statement)
            parameters = statement.compile().params
            identifiers = set()
            for value in parameters.values():
                identifiers.update(
                    value if isinstance(value, list) else [value])
            rows = [(row, source) for row in documents
                    if row.id in identifiers]
            return SimpleNamespace(
                first=lambda: rows[0] if rows else None,
                all=lambda: rows,
            )

    @asynccontextmanager
    async def session():
        yield Session()

    monkeypatch.setattr(search_module, "get_db_session", session)
    monkeypatch.setattr(search_module, "get_embedding_service_for_project",
                        AsyncMock(return_value=SimpleNamespace(
                            embeddings_client=object())))
    service = SearchService.__new__(SearchService)
    vectors = [(SimpleNamespace(id=str(row.id), page_content=f"资料 {index}"),
                0.95 - index / 100)
               for index, row in enumerate(documents)]
    service.vector_store_service = SimpleNamespace(
        similarity_search_for_project=AsyncMock(return_value=vectors))

    result = await service.semantic_search(
        "商品材质", project_id, collection_id, limit=hit_count,
        min_score=0.37, filters={"language": "zh"},
    )

    assert [row.document_id for row in result.results] == [
        row.id for row in documents]
    assert [row.relevance_score for row in result.results] == [
        score for _, score in vectors]
    assert all(row.metadata["source"] == "商品资料.md"
               for row in result.results)
    assert result.search_metadata.total_results == hit_count
    assert len(statements) == 1
    vector_search = service.vector_store_service.similarity_search_for_project
    vector_search.assert_awaited_once()


@pytest.mark.asyncio
async def test_empty_vector_results_skip_metadata(monkeypatch):
    project_id = uuid4()
    session = AsyncMock(side_effect=AssertionError("Unexpected metadata read"))
    monkeypatch.setattr(search_module, "get_db_session", session)
    monkeypatch.setattr(search_module, "get_embedding_service_for_project",
                        AsyncMock(return_value=SimpleNamespace(
                            embeddings_client=object())))
    service = SearchService.__new__(SearchService)
    service.vector_store_service = SimpleNamespace(
        similarity_search_for_project=AsyncMock(return_value=[]))
    result = await service.semantic_search("无匹配", project_id)
    assert result.results == []
    session.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("SAAS_TEST_DATABASE_URL"),
                    reason="Opt-in isolated PostgreSQL test")
async def test_batch_hydration_enforces_database_scope(monkeypatch):
    """Even incorrect vector hits cannot cross company or collection scope."""
    url = make_url(os.environ["SAAS_TEST_DATABASE_URL"])
    assert url.host in ("127.0.0.1", "localhost")
    engine = create_async_engine(url, hide_parameters=True)
    schema = "rag_search_batch_" + uuid4().hex
    try:
        async with engine.connect() as connection:
            outer = await connection.begin()
            try:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                await connection.execute(text(
                    f'SET LOCAL search_path TO "{schema}", public'))
                for model in (Collection, File, FileDocument):
                    await connection.run_sync(model.__table__.create)
                async with AsyncSession(
                    bind=connection, expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                ) as db:
                    own, other = uuid4(), uuid4()
                    collections = [Collection(
                        id=uuid4(), project_id=project,
                        display_name=f"Synthetic-{index}",
                        collection_type=CollectionType.file,
                    ) for index, project in enumerate((own, own, other))]
                    db.add_all(collections)
                    await db.flush()
                    sources = [File(
                        id=uuid4(), project_id=project,
                        collection_id=collection.id,
                        original_filename=name, file_size=10,
                        content_type="text/plain", storage_provider="local",
                        storage_path="synthetic-only",
                    ) for project, collection, name in (
                        (own, collections[0], "Own material"),
                        (other, collections[2], "Other company filename"),
                    )]
                    db.add_all(sources)
                    await db.flush()
                    accepted = document(own, collections[0].id, sources[0].id)
                    wrong_collection = document(
                        own, collections[1].id, sources[0].id)
                    wrong_company = document(
                        other, collections[2].id, sources[1].id)
                    foreign_source = document(
                        own, collections[0].id, sources[1].id)
                    db.add_all([accepted, wrong_collection,
                                wrong_company, foreign_source])
                    await db.commit()
                    reads = []

                    @asynccontextmanager
                    async def session():
                        reads.append(True)
                        yield db

                    monkeypatch.setattr(
                        search_module, "get_db_session", session)
                    monkeypatch.setattr(
                        search_module, "get_embedding_service_for_project",
                        AsyncMock(return_value=SimpleNamespace(
                            embeddings_client=object())),
                    )
                    hits = [wrong_company, foreign_source,
                            wrong_collection, accepted]
                    service = SearchService.__new__(SearchService)
                    service.vector_store_service = SimpleNamespace(
                        similarity_search_for_project=AsyncMock(return_value=[
                            (SimpleNamespace(id=str(row.id),
                                             page_content="Synthetic preview"),
                             0.9 - index / 10)
                            for index, row in enumerate(hits)
                        ]))
                    response = await service.semantic_search(
                        "商品材质", own, collections[0].id)
                    assert [row.document_id for row in response.results] == [
                        foreign_source.id, accepted.id]
                    assert response.results[0].metadata["source"] == "Unknown"
                    own_metadata = response.results[1].metadata
                    assert own_metadata["source"] == "Own material"
                    assert len(reads) == 1
            finally:
                await outer.rollback()
            remains = await connection.scalar(text(
                "SELECT count(*) FROM information_schema.schemata "
                "WHERE schema_name = :schema"), {"schema": schema})
            assert remains == 0
    finally:
        await engine.dispose()
