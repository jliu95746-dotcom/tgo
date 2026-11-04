from __future__ import annotations

import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.domain.services.listeners.wecom_listener import (
    MediaPreparationFailure,
    WeComChannelListener,
)
from app.domain.services.dispatcher import UnsentReplyFailure
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
        msg_type="text",
        source_type="wecom_kf",
        is_from_colleague=False,
        failure_notice_status=None,
        failure_notice_error=None,
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
@pytest.mark.parametrize(
    "error",
    [
        UnsentReplyFailure("AI stream failed with event workflow_failed"),
        MediaPreparationFailure("WeCom media preparation failed before reply"),
    ],
)
async def test_exhausted_media_failure_before_reply_enters_customer_notice_queue(
    error: Exception,
) -> None:
    listener = object.__new__(WeComChannelListener)
    session = FakeSession()
    record = make_record()
    record.msg_type = "voice"
    record.retry_count = 1

    await listener._finalize_failure(
        session,
        SimpleNamespace(id="platform-id"),
        record,
        error,
        max_retries=1,
    )

    assert record.status == "media_failed"
    assert record.next_attempt_at is None
    assert record.failure_notice_status is None


@pytest.mark.asyncio
async def test_exhausted_media_delivery_uncertainty_requires_manual_review() -> None:
    listener = object.__new__(WeComChannelListener)
    session = FakeSession()
    record = make_record()
    record.msg_type = "image"
    record.retry_count = 1

    await listener._finalize_failure(
        session,
        SimpleNamespace(id="platform-id"),
        record,
        TimeoutError("provider reply outcome unknown"),
        max_retries=1,
    )

    assert record.status == "dead"
    assert record.failure_notice_status == "needs_attention"


@pytest.mark.asyncio
async def test_media_delivery_uncertainty_never_retries_even_with_budget() -> None:
    listener = object.__new__(WeComChannelListener)
    session = FakeSession()
    record = make_record()
    record.msg_type = "image"

    await listener._finalize_failure(
        session,
        SimpleNamespace(id="platform-id"),
        record,
        TimeoutError("provider reply outcome unknown"),
        max_retries=3,
    )

    assert record.status == "dead"
    assert record.retry_count == 1
    assert record.next_attempt_at is None
    assert record.failure_notice_status == "needs_attention"


@pytest.mark.asyncio
async def test_media_preparation_failure_enters_notice_queue_without_sending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listener = object.__new__(WeComChannelListener)
    session = FakeSession()
    record = make_record()
    record.msg_type = "image"
    listener._session_factory = lambda: session
    listener._visitor_service = object()
    claimed = False

    async def claim(*_: object) -> SimpleNamespace | None:
        nonlocal claimed
        if claimed:
            return None
        claimed = True
        return record

    async def unavailable_visitor(*_: object) -> tuple[None, None, None]:
        raise RuntimeError("visitor registration unavailable")

    monkeypatch.setattr(listener, "_claim_next_record", claim)
    monkeypatch.setattr(listener, "_build_mapped_message", lambda *_: {})
    monkeypatch.setattr(listener, "_get_or_register_visitor", unavailable_visitor)

    await listener._process_pending_for_platform(
        SimpleNamespace(
            id="platform-id",
            cfg=SimpleNamespace(
                processing_batch_size=1, max_retry_attempts=0,
                native_handoff_enabled=False,
            ),
        )
    )

    assert record.status == "media_failed"
    assert record.failure_notice_status is None
    assert session.commits == 1


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


@pytest.mark.asyncio
async def test_message_failure_keeps_private_exception_out_of_record_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    listener = object.__new__(WeComChannelListener)
    session = FakeSession()
    record = make_record()
    with caplog.at_level(logging.ERROR):
        await listener._finalize_failure(
            session,
            SimpleNamespace(id="fixture-platform"),
            record,
            RuntimeError("private@example.com"),
            max_retries=1,
        )

    assert record.status == "failed"
    assert record.error_message == "RuntimeError"
    assert "private@example.com" not in caplog.text


@pytest.mark.asyncio
async def test_sync_failure_keeps_private_exception_out_of_persisted_job() -> None:
    listener = object.__new__(WeComChannelListener)
    job = SimpleNamespace(
        status="processing",
        retry_count=0,
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
        RuntimeError("private@example.com"),
        max_retries=1,
    )

    assert job.status == "failed"
    assert job.error_message == "RuntimeError"
