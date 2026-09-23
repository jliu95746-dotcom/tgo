"""Reject stale or cross-company file jobs before processing side effects."""

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.rag_service.models import Collection, CollectionType, File
from src.rag_service.tasks import document_processing_core as core
from .test_knowledge_tenant_boundary import tenant_knowledge  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["manual", "website_crawl"])
async def test_wrong_collection_stops_before_status_or_content(
    monkeypatch, source
):
    file = SimpleNamespace(
        id=uuid4(),
        collection_id=uuid4(),
        project_id=uuid4(),
        storage_metadata={"source": source},
    )
    monkeypatch.setattr(core, "_load_file_info", AsyncMock(return_value=file))
    effects = []
    for name in (
        "_update_file_status",
        "_load_document_content",
        "_handle_processing_error",
        "_update_website_page_status",
    ):
        effect = AsyncMock(side_effect=AssertionError("Unexpected processing"))
        if name in ("_handle_processing_error", "_update_website_page_status"):
            effect = AsyncMock()
        monkeypatch.setattr(core, name, effect)
        effects.append(effect)
    result = await core.process_file_async(file.id, uuid4())
    assert result.status == "skipped"
    for effect in effects:
        effect.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("SAAS_TEST_DATABASE_URL"),
    reason="Opt-in PostgreSQL isolation test",
)
@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "wrong_collection",
        "foreign_owner",
        "deleted_file",
        "deleted_collection",
    ],
)
async def test_postgres_file_job_scope(
    tenant_knowledge, monkeypatch, tmp_path, case  # noqa: F811
):
    state = tenant_knowledge
    company = state.tenants[0].company
    collection = Collection(
        project_id=company,
        display_name="Synthetic upload",
        collection_type=CollectionType.file,
    )
    state.db.add(collection)
    await state.db.flush()
    path = tmp_path / "synthetic.txt"
    path.write_text("synthetic private document", encoding="utf-8")
    file = File(
        project_id=company,
        collection_id=collection.id,
        original_filename=path.name,
        file_size=path.stat().st_size,
        content_type="text/plain",
        storage_provider="local",
        storage_path=str(path),
        status="pending",
    )
    state.db.add(file)
    if case == "deleted_file":
        file.deleted_at = datetime.now(timezone.utc)
    elif case == "deleted_collection":
        collection.deleted_at = datetime.now(timezone.utc)
    elif case == "foreign_owner":
        collection.project_id = state.tenants[1].company
    await state.db.commit()

    @asynccontextmanager
    async def session():
        yield state.db

    monkeypatch.setattr(core, "get_db_session", session)
    content = AsyncMock(return_value=[])
    monkeypatch.setattr(core, "_load_document_content", content)
    monkeypatch.setattr(core, "_chunk_documents", AsyncMock(return_value=[]))
    embedding = AsyncMock()
    monkeypatch.setattr(core, "_generate_document_embeddings", embedding)
    failure = AsyncMock()
    monkeypatch.setattr(core, "_handle_processing_error", failure)
    requested_collection = (
        state.tenants[1].site.id
        if case == "wrong_collection"
        else collection.id
    )
    result = await core.process_file_async(file.id, requested_collection)
    await state.db.refresh(file)
    if case == "valid":
        assert result.status == "completed"
        assert file.status == "completed"
        content.assert_awaited_once()
        embedding.assert_awaited_once()
    else:
        assert result.status == "skipped"
        assert file.status == "pending"
        content.assert_not_awaited()
        embedding.assert_not_awaited()
    failure.assert_not_awaited()
    assert path.read_text(encoding="utf-8") == "synthetic private document"
