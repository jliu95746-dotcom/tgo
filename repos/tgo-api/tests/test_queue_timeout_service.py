"""Worker integration commits lifecycle changes before any delivery attempt."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.services import queue_timeout_service as service
from tests.test_queue_lifecycle import queue_db, seed_queue  # noqa: F401


@pytest.mark.asyncio
async def test_processing_is_project_scoped_committed_and_restart_safe(
    queue_db, monkeypatch
):
    visitor, session, entry = seed_queue(queue_db)
    foreign = seed_queue(queue_db)[2]
    recovery = AsyncMock()
    monkeypatch.setattr(service, "recover_timeout_notices", recovery)

    async def verify_committed(db, entry_id):
        assert not db.in_transaction()
        assert entry_id == entry.id
        assert entry.status == "expired" and session.status == "closed"

    send = AsyncMock(side_effect=verify_committed)
    monkeypatch.setattr(service, "deliver_timeout_notice", send)
    assert (
        await service.process_queue_timeouts(queue_db, project_id=visitor.project_id)
        == 1
    )
    assert foreign.status == "waiting"
    assert (
        await service.process_queue_timeouts(queue_db, project_id=visitor.project_id)
        == 0
    )
    send.assert_awaited_once()
    assert recovery.await_count == 2  # Recovery runs even when no new entries expire.


@pytest.mark.asyncio
async def test_delivery_failure_keeps_committed_timeout_for_recovery(
    queue_db, monkeypatch
):
    visitor, session, entry = seed_queue(queue_db)
    monkeypatch.setattr(
        service, "deliver_timeout_notice", AsyncMock(side_effect=RuntimeError())
    )
    recovery = AsyncMock()
    monkeypatch.setattr(service, "recover_timeout_notices", recovery)
    assert await service.process_queue_timeouts(queue_db) == 1
    assert entry.status == "expired" and visitor.service_status == "closed"
    assert entry.extra_metadata["queue_timeout_notice"]["history_status"] == "pending"
    recovery.assert_awaited_once()


@pytest.mark.asyncio
async def test_future_entry_does_not_expire_and_recovery_still_runs(
    queue_db, monkeypatch
):
    visitor, session, entry = seed_queue(
        queue_db, expired_at=datetime.utcnow() + timedelta(minutes=5)
    )
    send, recovery = AsyncMock(), AsyncMock()
    monkeypatch.setattr(service, "deliver_timeout_notice", send)
    monkeypatch.setattr(service, "recover_timeout_notices", recovery)
    assert await service.process_queue_timeouts(queue_db) == 0
    send.assert_not_awaited()
    recovery.assert_awaited_once()
    assert entry.status == "waiting"
