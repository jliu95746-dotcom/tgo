from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.domain.services.listeners.wecom_listener import WeComChannelListener
from app.api.wecom_utils import WeComSyncContinuation


class FakeSession:
    def __init__(self, job: SimpleNamespace | None = None) -> None:
        self.commits = 0
        self.job = job

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def get(self, *_: object) -> SimpleNamespace | None:
        return self.job

    async def commit(self) -> None:
        self.commits += 1


def make_record() -> SimpleNamespace:
    return SimpleNamespace(
        message_id="customer-message",
        retry_count=0,
        status="processing",
        processed_at=None,
        processing_started_at=datetime.now(timezone.utc),
        lease_expires_at=datetime.now(timezone.utc),
        next_attempt_at=None,
        error_message=None,
        ai_reply=None,
    )


@pytest.mark.asyncio
async def test_message_failure_retries_then_moves_to_dead_state() -> None:
    listener = object.__new__(WeComChannelListener)
    session = FakeSession()
    platform = SimpleNamespace(id="platform-id")
    record = make_record()

    await listener._finalize_failure(
        session,
        platform,
        record,
        RuntimeError("temporary failure"),
        max_retries=1,
    )

    assert record.status == "failed"
    assert record.retry_count == 1
    assert record.next_attempt_at is not None
    assert record.lease_expires_at is None

    await listener._finalize_failure(
        session,
        platform,
        record,
        RuntimeError("permanent failure"),
        max_retries=1,
    )

    assert record.status == "dead"
    assert record.retry_count == 2
    assert record.next_attempt_at is None
    assert session.commits == 2


@pytest.mark.asyncio
async def test_no_reply_success_clears_processing_lease() -> None:
    listener = object.__new__(WeComChannelListener)
    session = FakeSession()
    record = make_record()

    await listener._finalize_success(session, record, None)

    assert record.status == "completed_no_reply"
    assert record.processing_started_at is None
    assert record.lease_expires_at is None
    assert session.commits == 1


@pytest.mark.asyncio
async def test_sync_continuation_requeues_without_consuming_retry() -> None:
    listener = object.__new__(WeComChannelListener)
    job = SimpleNamespace(
        status="processing",
        retry_count=2,
        processing_started_at=datetime.now(timezone.utc),
        lease_expires_at=datetime.now(timezone.utc),
        next_attempt_at=None,
        completed_at=None,
        error_message=None,
    )
    session = FakeSession(job)
    listener._session_factory = lambda: session

    await listener._finalize_sync_job(
        SimpleNamespace(),
        WeComSyncContinuation("continue"),
        max_retries=3,
    )

    assert job.status == "pending"
    assert job.retry_count == 2
    assert job.next_attempt_at is not None
    assert job.lease_expires_at is None
