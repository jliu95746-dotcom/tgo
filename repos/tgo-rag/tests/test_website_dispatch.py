"""Failed dispatch cannot strand files or overwrite newer processing state."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.rag_service.models import File
from src.rag_service.services import website_dispatch as service


def test_failure_reason_is_a_nullable_persisted_column():
    column = File.__table__.c.get("error_message")
    assert column is not None and column.nullable


@pytest.fixture
def current(monkeypatch):
    page = SimpleNamespace(status="extracted", error_message=None)
    file = SimpleNamespace(id=uuid4(), status="pending", error_message=None)
    db = AsyncMock()

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(service, "get_db_session", session)
    lookup = AsyncMock(return_value=(page, file))
    monkeypatch.setattr(service, "current_source", lookup)
    return page, file, db, lookup


@pytest.mark.asyncio
async def test_prepare_commits_page_state_before_dispatch(current):
    page, file, db, _ = current
    assert await service.prepare_website_dispatch(file)
    assert (page.status, file.status) == ("processing", "pending")
    db.commit.assert_awaited_once()
    assert not await service.prepare_website_dispatch(file)


@pytest.mark.asyncio
@pytest.mark.parametrize("page_status", ["extracted", "processing"])
async def test_dispatch_failure_atomically_fails_page_and_pending_file(
    current, page_status,
):
    page, file, db, _ = current
    page.status = page_status
    assert await service.fail_website_dispatch(file, "提交失败，请重试。")
    assert page.status == file.status == "failed"
    assert page.error_message == file.error_message == "提交失败，请重试。"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("page_status,file_status", [
    ("processing", "processing"), ("processing", "chunking_documents"),
    ("processing", "generating_embeddings"), ("processed", "completed"),
    ("pending", "pending"), ("failed", "failed"),
])
async def test_late_failure_does_not_overwrite_claimed_or_new_state(
    current, page_status, file_status,
):
    page, file, db, _ = current
    page.status, file.status = page_status, file_status
    assert not await service.fail_website_dispatch(file, "late failure")
    assert (page.status, file.status) == (page_status, file_status)
    assert page.error_message is None and file.error_message is None
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_deleted_or_replaced_source_is_never_changed(current):
    _, file, db, lookup = current
    lookup.return_value = None
    assert not await service.prepare_website_dispatch(file)
    assert not await service.fail_website_dispatch(file, "late failure")
    db.commit.assert_not_awaited()
