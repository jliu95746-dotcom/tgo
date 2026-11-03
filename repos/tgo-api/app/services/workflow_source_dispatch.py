"""Recoverable dispatch from committed manual-close facts to fixed releases."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.session_command_delivery import SessionCommandDelivery
from app.models.workflow_event_subscription import (
    WorkflowEventDelivery,
    WorkflowEventSubscription,
)
from app.schemas.workflow_event_subscriptions import (
    WorkflowEventSourceBinding,
    WorkflowEventSourceCheck,
    WorkflowSourceEventRequest,
)
from app.services.workflow_client import workflow_client
from app.services.workflow_event_snapshot import (
    SnapshotUnavailable,
    load_closed_session_snapshot,
)
from app.services.workflow_event_subscriptions import (
    current_subscription_authorized,
    verify_source_binding,
)
from app.services.workflow_event_crypto import (
    decrypt_event_payload,
    encrypt_event_payload,
    ensure_event_payload_crypto,
)
from app.services.event_keyring import EventKeyringError

MAX_ATTEMPTS = 8
MAX_AGE = timedelta(minutes=30)
LEASE_TIME = timedelta(minutes=2)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


@dataclass(frozen=True)
class SourceEventClaim:
    delivery_id: UUID
    attempt: int


def collect_source_events(db: Session, limit: int = 50) -> int:
    """Collect oldest unrecorded facts created after each opt-in."""
    sub, source = WorkflowEventSubscription, SessionCommandDelivery
    recorded = (
        select(WorkflowEventDelivery.id)
        .where(
            WorkflowEventDelivery.subscription_id == sub.id,
            WorkflowEventDelivery.source_command_id == source.id,
        )
        .exists()
    )
    candidates = db.execute(
        select(sub, source)
        .join(
            source,
            and_(
                source.project_id == sub.project_id,
                source.kind == "close",
                source.created_at >= sub.enabled_at,
            ),
        )
        .where(
            sub.enabled.is_(True),
            sub.release_id.is_not(None),
            sub.release_digest.is_not(None),
            ~recorded,
        )
        .order_by(source.created_at, source.id, sub.id)
        .limit(limit)
        .with_for_update(of=sub, skip_locked=True)
        .execution_options(populate_existing=True)
    ).all()
    count = 0
    for subscription, command in candidates:
        if not subscription.release_id or not subscription.release_digest:
            continue
        authorized = current_subscription_authorized(db, subscription)
        delivery = WorkflowEventDelivery(
            subscription_id=subscription.id,
            subscription_revision=subscription.revision,
            project_id=subscription.project_id,
            workflow_id=subscription.workflow_id,
            source_command_id=command.id,
            session_id=command.session_id,
            release_id=subscription.release_id,
            release_digest=subscription.release_digest,
            status="pending" if authorized else "cancelled",
            last_error=None if authorized else "subscription_authority_lost",
        )
        try:
            with db.begin_nested():
                db.add(delivery)
                db.flush()
            count += 1
        except IntegrityError:
            # Another collector won; this subscription/source pair is unique.
            continue
    db.commit()
    return count


def claim_source_event(db: Session) -> SourceEventClaim | None:
    for _ in range(50):
        now = utc_now()
        row = db.scalar(
            select(WorkflowEventDelivery)
            .where(
                or_(
                    and_(
                        WorkflowEventDelivery.status == "pending",
                        WorkflowEventDelivery.next_attempt_at <= now,
                    ),
                    and_(
                        WorkflowEventDelivery.status == "sending",
                        WorkflowEventDelivery.lease_until <= now,
                    ),
                )
            )
            .order_by(
                WorkflowEventDelivery.next_attempt_at,
                WorkflowEventDelivery.created_at,
            )
            .with_for_update(skip_locked=True)
            .limit(1)
            .execution_options(populate_existing=True)
        )
        if row is None:
            db.commit()
            return None
        if (
            now - _utc(row.created_at) >= MAX_AGE
            or row.attempts >= MAX_ATTEMPTS
        ):
            row.status = "failed"
            row.last_error = (
                "delivery_expired"
                if now - _utc(row.created_at) >= MAX_AGE
                else "attempts_exhausted"
            )
            row.encrypted_payload = None
            row.lease_until = None
            db.commit()
            continue
        row.status = "sending"
        row.attempts += 1
        row.lease_until = now + LEASE_TIME
        claim = SourceEventClaim(row.id, row.attempts)
        db.commit()
        return claim
    return None


def _owned(
    db: Session, claim: SourceEventClaim
) -> WorkflowEventDelivery | None:
    return db.scalar(
        select(WorkflowEventDelivery)
        .where(
            WorkflowEventDelivery.id == claim.delivery_id,
            WorkflowEventDelivery.status == "sending",
            WorkflowEventDelivery.attempts == claim.attempt,
        )
        .execution_options(populate_existing=True)
    )


def finish_source_event(
    db: Session,
    claim: SourceEventClaim,
    status: Literal["accepted", "failed", "cancelled"],
    *,
    execution_id: str | None = None,
    error: str | None = None,
) -> bool:
    updated = db.execute(
        update(WorkflowEventDelivery)
        .where(
            WorkflowEventDelivery.id == claim.delivery_id,
            WorkflowEventDelivery.status == "sending",
            WorkflowEventDelivery.attempts == claim.attempt,
        )
        .values(
            status=status,
            execution_id=execution_id,
            last_error=error,
            encrypted_payload=None,
            lease_until=None,
        )
        .returning(WorkflowEventDelivery.id)
    ).scalar_one_or_none()
    db.commit()
    return updated is not None


def _retry(db: Session, claim: SourceEventClaim, error: str) -> None:
    row = _owned(db, claim)
    if row is None:
        db.rollback()
        return
    if (
        claim.attempt >= MAX_ATTEMPTS
        or utc_now() - _utc(row.created_at) >= MAX_AGE
    ):
        finish_source_event(db, claim, "failed", error="attempts_exhausted")
        return
    db.execute(
        update(WorkflowEventDelivery)
        .where(
            WorkflowEventDelivery.id == claim.delivery_id,
            WorkflowEventDelivery.status == "sending",
            WorkflowEventDelivery.attempts == claim.attempt,
        )
        .values(
            status="pending",
            lease_until=None,
            last_error=error,
            next_attempt_at=utc_now()
            + timedelta(seconds=min(300, 5 * 2**claim.attempt)),
        )
    )
    db.commit()


def _check(row: WorkflowEventDelivery) -> WorkflowEventSourceCheck:
    return WorkflowEventSourceCheck(
        project_id=row.project_id,
        workflow_id=row.workflow_id,
        release_id=row.release_id,
        release_digest=row.release_digest,
        source_binding=WorkflowEventSourceBinding(
            subscription_id=row.subscription_id,
            revision=row.subscription_revision,
            delivery_id=row.id,
            source_command_id=row.source_command_id,
            session_id=row.session_id,
        ),
    )


async def deliver_source_event(db: Session, claim: SourceEventClaim) -> None:
    """Freeze before POST and retry uncertain results with the same body/ID."""
    try:
        row = _owned(db, claim)
        if row is None:
            db.rollback()
            return
        check = _check(row)
        verify_source_binding(db, check)
        encrypted = row.encrypted_payload
        db.commit()
        context = await workflow_client.get_event_test_context(
            check.workflow_id, str(check.project_id)
        )
        if (
            context.workflow_id != check.workflow_id
            or context.event_type != check.event_type
            or context.release_id != check.release_id
            or context.release_digest != check.release_digest
        ):
            finish_source_event(
                db, claim, "cancelled", error="publication_changed"
            )
            return
        if encrypted:
            plaintext = decrypt_event_payload(encrypted)
            request = WorkflowSourceEventRequest.model_validate_json(plaintext)
            if (
                request.source_binding != check.source_binding
                or request.expected_release_id != check.release_id
                or request.expected_release_digest != check.release_digest
            ):
                raise ValueError("Frozen event binding mismatch")
        else:
            ensure_event_payload_crypto()
            verify_source_binding(db, check)
            subscription = db.get(
                WorkflowEventSubscription, check.source_binding.subscription_id
            )
            source = db.get(
                SessionCommandDelivery, check.source_binding.source_command_id
            )
            if subscription is None or source is None:
                raise HTTPException(409, "source_unavailable")
            data = await load_closed_session_snapshot(db, subscription, source)
            # The loader may await IM. Recheck authority and claim afterwards.
            row = _owned(db, claim)
            if row is None:
                db.rollback()
                return
            verify_source_binding(db, check)
            request = WorkflowSourceEventRequest(
                expected_release_id=check.release_id,
                expected_release_digest=check.release_digest,
                source_binding=check.source_binding,
                data=data,
            )
            frozen = encrypt_event_payload(request.model_dump_json())
            saved = db.execute(
                update(WorkflowEventDelivery)
                .where(
                    WorkflowEventDelivery.id == claim.delivery_id,
                    WorkflowEventDelivery.status == "sending",
                    WorkflowEventDelivery.attempts == claim.attempt,
                )
                .values(encrypted_payload=frozen)
                .returning(WorkflowEventDelivery.id)
            ).scalar_one_or_none()
            db.commit()
            if saved is None:
                return
        row = _owned(db, claim)
        if row is None:
            db.rollback()
            return
        verify_source_binding(db, check)
        if utc_now() - _utc(row.created_at) >= MAX_AGE:
            finish_source_event(db, claim, "failed", error="delivery_expired")
            return
        db.commit()
        result = await workflow_client.send_source_event(
            check.workflow_id,
            str(check.project_id),
            claim.delivery_id,
            request,
        )
        finish_source_event(
            db, claim, "accepted", execution_id=result.execution_id
        )
    except SnapshotUnavailable as exc:
        db.rollback()
        finish_source_event(db, claim, "failed", error=exc.code)
    except HTTPException as exc:
        db.rollback()
        if exc.status_code in {403, 404, 409, 422, 413}:
            finish_source_event(
                db,
                claim,
                "cancelled",
                error="source_or_publication_unavailable",
            )
        else:
            _retry(db, claim, "workflow_temporarily_unavailable")
    except EventKeyringError:
        db.rollback()
        _retry(db, claim, "event_delivery_key_unavailable")
    except ValueError:
        db.rollback()
        finish_source_event(
            db, claim, "failed", error="frozen_payload_invalid"
        )
    except Exception:
        db.rollback()
        # Never persist exception text or the customer's excerpt.
        _retry(db, claim, "source_delivery_unavailable")
