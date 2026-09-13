"""Recrawl requests must be exclusive and report queue failures honestly."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.rag_service.routers import websites
from src.rag_service.tasks.website_crawling import crawl_page_task
from src.rag_service.services import knowledge_versions


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", ["pending", "retry", "crawling", "fetched", "extracted", "processing"]
)
async def test_busy_page_cannot_be_reset_or_queued_again(monkeypatch, status):
    page = SimpleNamespace(status=status, error_message=None)
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: page)
    delay = Mock()
    monkeypatch.setattr(crawl_page_task, "delay", delay)
    with pytest.raises(HTTPException) as error:
        await websites.recrawl_page(uuid4(), uuid4(), db)
    assert error.value.status_code == 409
    assert page.status == status
    db.commit.assert_not_awaited()
    delay.assert_not_called()
    statement = db.execute.await_args.args[0]
    assert statement._for_update_arg is not None
    assert "deleted_at IS NULL" in str(statement)


@pytest.mark.asyncio
async def test_broker_failure_does_not_return_queued_success(monkeypatch):
    monkeypatch.setattr(knowledge_versions, 'find_source', AsyncMock(return_value=None))
    page_id, project_id = uuid4(), uuid4()
    page = SimpleNamespace(status="processed", error_message=None)
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: page)
    monkeypatch.setattr(
        crawl_page_task, "delay", Mock(side_effect=RuntimeError("redis://private"))
    )
    with pytest.raises(HTTPException) as error:
        await websites.recrawl_page(page_id, project_id, db)
    assert error.value.status_code == 503
    assert "private" not in str(error.value.detail)
    failure_update = str(db.execute.await_args.args[0])
    assert "UPDATE rag_website_pages" in failure_update
    assert "status" in failure_update and "project_id" in failure_update
    assert db.commit.await_count == 2


@pytest.mark.asyncio
async def test_terminal_page_is_queued_after_pending_commit(monkeypatch):
    monkeypatch.setattr(knowledge_versions, 'find_source', AsyncMock(return_value=None))
    page_id, project_id = uuid4(), uuid4()
    page = SimpleNamespace(status="failed", error_message="old error")
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: page)
    events = []
    db.commit.side_effect = lambda: events.append("commit")

    def delay(*args, **kwargs):
        assert page.status == "pending" and page.error_message is None
        events.append("dispatch")
        return SimpleNamespace(id="owned-task")

    monkeypatch.setattr(crawl_page_task, "delay", delay)
    result = await websites.recrawl_page(page_id, project_id, db)
    assert result.success and events == ["commit", "dispatch"]


@pytest.mark.asyncio
async def test_versioned_page_cannot_bypass_draft_publication(monkeypatch):
    page = SimpleNamespace(status='processed', error_message=None)
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: page)
    monkeypatch.setattr(knowledge_versions, 'find_source', AsyncMock(return_value=object()))
    delay = Mock()
    monkeypatch.setattr(crawl_page_task, 'delay', delay)
    with pytest.raises(HTTPException) as error:
        await websites.recrawl_page(uuid4(), uuid4(), db)
    assert error.value.status_code == 409
    assert page.status == 'processed'
    delay.assert_not_called()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_page_storage_cleanup_only_happens_after_commit(monkeypatch):
    page_id, project_id, file_id = uuid4(), uuid4(), uuid4()
    page = SimpleNamespace(id=page_id)
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: page)
    events = []
    monkeypatch.setattr(
        websites, "_delete_page_cascade",
        AsyncMock(return_value=[(file_id, "owned-fixture-path")]),
    )
    monkeypatch.setattr(
        websites, "cleanup_deleted_page_files", lambda files: events.append("cleanup"),
        raising=False,
    )
    db.commit.side_effect = lambda: events.append("commit")
    await websites.delete_page(page_id, project_id, db)
    assert events == ["commit", "cleanup"]


@pytest.mark.asyncio
async def test_page_delete_captures_all_generated_versions(monkeypatch):
    page = SimpleNamespace(
        id=uuid4(), file_id=uuid4(), project_id=uuid4(), collection_id=uuid4(),
    )
    older_file = uuid4()
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: [older_file, page.file_id]),
    )
    remove = AsyncMock(return_value="owned-path")
    monkeypatch.setattr(websites, "_delete_file_and_documents", remove)
    paths = await websites._delete_page_cascade(db, page)
    assert {file_id for file_id, _ in paths} == {older_file, page.file_id}
    statement = str(db.execute.await_args.args[0])
    assert "storage_metadata" in statement and "project_id" in statement
    assert "collection_id" in statement
    assert remove.await_count == 2
