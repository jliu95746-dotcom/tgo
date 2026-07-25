"""Shared durable claim, retry, lease recovery, and dead-letter semantics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession


async def claim_inbox_batch(
    session: AsyncSession,
    model: Any,
    *,
    platform_id: UUID,
    batch_size: int,
    max_retry_attempts: int,
    lease_seconds: int = 120,
) -> list[Any]:
    """Claim eligible records while recovering workers with expired leases."""

    now = datetime.now(timezone.utc)
    eligible = or_(
        model.status == "pending",
        and_(
            model.status == "failed",
            model.retry_count < max_retry_attempts,
            or_(
                model.next_attempt_at.is_(None),
                model.next_attempt_at <= now,
            ),
        ),
        and_(
            model.status == "processing",
            or_(
                model.lease_expires_at.is_(None),
                model.lease_expires_at <= now,
            ),
        ),
    )
    records = (
        await session.execute(
            select(model)
            .where(model.platform_id == platform_id, eligible)
            .order_by(model.fetched_at.asc())
            .with_for_update(skip_locked=True)
            .limit(max(1, batch_size))
        )
    ).scalars().all()

    lease_window = max(10, lease_seconds)
    for position, record in enumerate(records, start=1):
        record.status = "processing"
        record.processing_started_at = now
        record.lease_expires_at = now + timedelta(
            seconds=lease_window * position
        )
        record.next_attempt_at = None
        record.error_message = None
    if records:
        await session.commit()
    return list(records)


def finalize_inbox_success(
    record: Any,
    *,
    completed_status: str = "completed",
) -> None:
    """Complete a claimed record and clear all retry/lease state."""

    now = datetime.now(timezone.utc)
    record.status = completed_status
    record.processed_at = now
    record.processing_started_at = None
    record.lease_expires_at = None
    record.next_attempt_at = None
    record.error_message = None


def finalize_inbox_failure(
    record: Any,
    error: Exception,
    *,
    max_retry_attempts: int,
) -> None:
    """Schedule retry or move an exhausted record to dead letter."""

    now = datetime.now(timezone.utc)
    retry_count = int(record.retry_count or 0) + 1
    record.retry_count = retry_count
    record.status = "dead" if retry_count >= max_retry_attempts else "failed"
    record.processed_at = now
    record.processing_started_at = None
    record.lease_expires_at = None
    record.error_message = str(error)[:2000]
    record.next_attempt_at = (
        None
        if record.status == "dead"
        else now + timedelta(seconds=max(1, 2**retry_count))
    )
