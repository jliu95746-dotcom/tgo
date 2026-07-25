"""Contract tests for the channel-neutral Inbox reliability state machine."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.db.models import (
    DingTalkInbox,
    EmailInbox,
    FeishuInbox,
    SlackInbox,
    TelegramInbox,
    WuKongIMInbox,
)
from app.domain.services.inbox_reliability import (
    finalize_inbox_failure,
    finalize_inbox_success,
)


def test_all_generic_inboxes_expose_lease_and_retry_schedule_fields() -> None:
    for model in (
        EmailInbox,
        WuKongIMInbox,
        FeishuInbox,
        DingTalkInbox,
        TelegramInbox,
        SlackInbox,
    ):
        columns = model.__table__.columns
        assert "processing_started_at" in columns
        assert "lease_expires_at" in columns
        assert "next_attempt_at" in columns


def test_finalize_inbox_success_clears_claim_state() -> None:
    record = SimpleNamespace(
        status="processing",
        processed_at=None,
        processing_started_at=datetime.now(timezone.utc),
        lease_expires_at=datetime.now(timezone.utc),
        next_attempt_at=datetime.now(timezone.utc),
        error_message="old error",
    )

    finalize_inbox_success(record)

    assert record.status == "completed"
    assert record.processed_at is not None
    assert record.processing_started_at is None
    assert record.lease_expires_at is None
    assert record.next_attempt_at is None
    assert record.error_message is None


def test_finalize_inbox_failure_schedules_retry_then_moves_to_dead() -> None:
    retrying = SimpleNamespace(
        status="processing",
        retry_count=0,
        processed_at=None,
        processing_started_at=datetime.now(timezone.utc),
        lease_expires_at=datetime.now(timezone.utc),
        next_attempt_at=None,
        error_message=None,
    )
    exhausted = SimpleNamespace(
        status="processing",
        retry_count=2,
        processed_at=None,
        processing_started_at=datetime.now(timezone.utc),
        lease_expires_at=datetime.now(timezone.utc),
        next_attempt_at=None,
        error_message=None,
    )

    finalize_inbox_failure(
        retrying,
        RuntimeError("temporary"),
        max_retry_attempts=3,
    )
    finalize_inbox_failure(
        exhausted,
        RuntimeError("permanent"),
        max_retry_attempts=3,
    )

    assert retrying.status == "failed"
    assert retrying.retry_count == 1
    assert retrying.next_attempt_at is not None
    assert retrying.lease_expires_at is None
    assert exhausted.status == "dead"
    assert exhausted.retry_count == 3
    assert exhausted.next_attempt_at is None
