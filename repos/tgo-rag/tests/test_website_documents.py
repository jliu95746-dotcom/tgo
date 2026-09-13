"""Website replacements publish only a complete, current generation."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from src.rag_service.models import File, WebsitePage
from src.rag_service.services import website_documents as service
from src.rag_service.tasks import document_processing_core as core


@pytest.fixture
def source(monkeypatch):
    project_id, collection_id, page_id, file_id = [uuid4() for _ in range(4)]
    file = File(
        id=file_id,
        project_id=project_id,
        collection_id=collection_id,
        status="pending",
        storage_metadata={
            "source": "website_crawl",
            "page_id": str(page_id),
        },
    )
    page = WebsitePage(
        id=page_id,
        project_id=project_id,
        collection_id=collection_id,
        file_id=file_id,
        status="processing",
    )
    db = AsyncMock()
    db.add_all = Mock()
    db.execute.return_value = SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: [])
    )

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(service, "get_db_session", session)
    monkeypatch.setattr(service, "current_source", AsyncMock(return_value=(page, file)))
    embedding = SimpleNamespace(
        generate_embeddings_batch=AsyncMock(return_value=[[0.1] * 1536]),
        get_embedding_model=lambda: "owned-test-model",
    )
    monkeypatch.setattr(
        service, "get_embedding_service_for_project", AsyncMock(return_value=embedding)
    )
    cleanup = AsyncMock()
    monkeypatch.setattr(service, "cleanup_retired_files", cleanup)
    chunk = {
        "id": uuid4(),
        "content": "新版测试资料",
        "character_count": 6,
        "token_count": 6,
        "chunk_index": 0,
        "document_type": "paragraph",
        "metadata": {},
    }
    return page, file, db, embedding, cleanup, [chunk]


@pytest.mark.asyncio
async def test_duplicate_file_task_is_not_claimed_twice(source):
    _, file, db, *_ = source
    assert await service.claim_website_file(file)
    assert file.status == "processing"
    assert not await service.claim_website_file(file)
    assert db.commit.await_count == 1


@pytest.mark.asyncio
async def test_completed_file_is_never_downgraded(source):
    page, file, db, *_ = source
    page.status, file.status = "processed", "completed"
    assert not await service.claim_website_file(file)
    assert file.status == "completed"
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "vectors", [[], [[0.1]], [[0.0] * 1536], [[float("nan")] * 1536]]
)
async def test_invalid_embeddings_do_not_write_or_retire_old_documents(source, vectors):
    _, file, db, embedding, cleanup, chunks = source
    file.status = "generating_embeddings"
    embedding.generate_embeddings_batch.return_value = vectors
    with pytest.raises(ValueError):
        await service.publish_website_documents(file, chunks)
    db.add_all.assert_not_called()
    db.commit.assert_not_awaited()
    cleanup.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_failure_leaves_old_documents_untouched(source):
    _, file, db, embedding, cleanup, chunks = source
    embedding.generate_embeddings_batch.side_effect = RuntimeError("provider failed")
    with pytest.raises(RuntimeError):
        await service.publish_website_documents(file, chunks)
    db.execute.assert_not_awaited()
    cleanup.assert_not_awaited()


@pytest.mark.asyncio
async def test_deleted_or_superseded_page_cannot_publish(source, monkeypatch):
    _, file, db, _, cleanup, chunks = source
    monkeypatch.setattr(service, "current_source", AsyncMock(return_value=None))
    assert not await service.publish_website_documents(file, chunks)
    db.add_all.assert_not_called()
    db.commit.assert_not_awaited()
    cleanup.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_documents_and_status_commit_before_cleanup(source):
    page, file, db, _, cleanup, chunks = source
    file.status = "generating_embeddings"
    events = []
    db.commit.side_effect = lambda: events.append("commit")
    cleanup.side_effect = lambda *args: events.append("cleanup")
    assert await service.publish_website_documents(file, chunks)
    assert events == ["commit", "cleanup"]
    assert page.status == "processed" and file.status == "completed"
    assert file.document_count == 1 and file.total_tokens == 6
    document = db.add_all.call_args.args[0][0]
    assert document.content == "新版测试资料"
    assert document.embedding_dimensions == 1536


@pytest.mark.asyncio
async def test_website_pipeline_never_uses_incremental_document_store(
    source, monkeypatch
):
    _, file, _, _, _, chunks = source
    monkeypatch.setattr(core, "_load_file_info", AsyncMock(return_value=file))
    monkeypatch.setattr(core, "_update_file_status", AsyncMock())
    monkeypatch.setattr(core, "_load_document_content", AsyncMock(return_value=[]))
    monkeypatch.setattr(core, "_chunk_documents", AsyncMock(return_value=chunks))
    monkeypatch.setattr(service, "claim_website_file", AsyncMock(return_value=True))
    publisher = AsyncMock(return_value=True)
    monkeypatch.setattr(service, "publish_website_documents", publisher)
    incremental = AsyncMock()
    monkeypatch.setattr(core, "_store_document_chunks", incremental)
    result = await core.process_file_async(file.id, file.collection_id)
    assert result.status == "completed"
    publisher.assert_awaited_once()
    incremental.assert_not_awaited()


def test_retired_path_must_be_exact_owned_uuid_under_upload_root(tmp_path):
    file_id = uuid4()
    expected = tmp_path / str(file_id)
    assert service.owned_storage_path(str(tmp_path), file_id, str(expected)) == expected
    for unsafe in (tmp_path, tmp_path / "manual.txt", tmp_path.parent / str(file_id)):
        with pytest.raises(ValueError):
            service.owned_storage_path(str(tmp_path), file_id, str(unsafe))


@pytest.mark.asyncio
async def test_old_document_retirement_shares_new_publication_commit(source):
    _, file, db, _, _, chunks = source
    file.status = "generating_embeddings"
    old = File(id=uuid4(), status="completed", document_count=3, total_tokens=20)
    db.execute.return_value = SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: [old]),
    )
    assert await service.publish_website_documents(file, chunks)
    assert old.status == "archived" and old.deleted_at is not None
    assert old.document_count == old.total_tokens == 0
    retirement = db.execute.await_args_list[1].args[0]
    sql = str(retirement)
    assert "DELETE FROM rag_file_documents" in sql
    assert "project_id" in sql and "collection_id" in sql and "file_id IN" in sql


@pytest.mark.asyncio
async def test_cleanup_failure_does_not_fail_published_version(source):
    page, file, _, _, cleanup, chunks = source
    file.status = "generating_embeddings"
    cleanup.side_effect = OSError("fixture cleanup denied")
    assert await service.publish_website_documents(file, chunks)
    assert page.status == "processed" and file.status == "completed"


@pytest.mark.asyncio
async def test_duplicate_pipeline_does_not_reset_file_status(source, monkeypatch):
    _, file, _, _, _, _ = source
    file.status = "completed"
    monkeypatch.setattr(core, "_load_file_info", AsyncMock(return_value=file))
    status = AsyncMock()
    monkeypatch.setattr(core, "_update_file_status", status)
    monkeypatch.setattr(service, "claim_website_file", AsyncMock(return_value=False))
    result = await core.process_file_async(file.id, file.collection_id)
    assert result.status == "skipped"
    status.assert_not_awaited()
