"""Queue transitions use the same visitor -> queue lock order as assignment."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import Platform, Visitor, VisitorSession, VisitorWaitingQueue
from app.schemas.queue_timeout import QUEUE_TIMEOUT_NOTICE_KEY, QueueTimeoutNotice
from app.services.queue_events import schedule_queue_update
from app.utils.encoding import build_visitor_channel_id


def lock_waiting_entry(
    db: Session, visitor: Visitor, entry_id: UUID
) -> VisitorWaitingQueue | None:
    """Caller holds the visitor lock; refresh stale ORM state before deciding."""
    if visitor.service_status != "queued":
        return None
    entry: VisitorWaitingQueue | None = (
        db.query(VisitorWaitingQueue)
        .filter(
            VisitorWaitingQueue.id == entry_id,
            VisitorWaitingQueue.visitor_id == visitor.id,
            VisitorWaitingQueue.project_id == visitor.project_id,
            VisitorWaitingQueue.status == "waiting",
            or_(
                VisitorWaitingQueue.expired_at.is_(None),
                VisitorWaitingQueue.expired_at > datetime.utcnow(),
            ),
        )
        .populate_existing()
        .with_for_update()
        .first()
    )
    if entry is None:
        return None
    latest = (
        db.query(VisitorSession)
        .filter(
            VisitorSession.visitor_id == visitor.id,
            VisitorSession.project_id == visitor.project_id,
        )
        .order_by(VisitorSession.created_at.desc(), VisitorSession.id.desc())
        .first()
    )
    if latest is not None and (
        latest.id != entry.session_id
        or latest.status != "open"
        or latest.staff_id is not None
    ):
        return None
    return entry


def assign_waiting_entries(db: Session, visitor: Visitor, staff_id: UUID) -> None:
    """Finish pending queue records in the assignment transaction, not afterward."""
    # SessionLocal disables autoflush. Persist the claimed entry's attempt before
    # populate_existing refreshes it, while retaining the owning visitor lock.
    db.flush()
    entries = (
        db.query(VisitorWaitingQueue)
        .filter(
            VisitorWaitingQueue.project_id == visitor.project_id,
            VisitorWaitingQueue.visitor_id == visitor.id,
            VisitorWaitingQueue.status == "waiting",
        )
        .populate_existing()
        .with_for_update()
        .all()
    )
    for entry in entries:
        entry.assign_to_staff(staff_id)
    if entries:
        schedule_queue_update(db, visitor.project_id)


def expire_entry(db: Session, entry_id: UUID) -> bool:
    """Expire one record and its still-current waiting session atomically.

    This function never commits or sends. Its caller commits before attempting
    delivery, so rollback cannot leak a timeout notice to a visitor.
    """
    identity = (
        db.query(VisitorWaitingQueue.project_id, VisitorWaitingQueue.visitor_id)
        .filter(VisitorWaitingQueue.id == entry_id)
        .first()
    )
    if identity is None:
        return False
    visitor: Visitor | None = (
        db.query(Visitor)
        .filter(
            Visitor.id == identity.visitor_id,
            Visitor.project_id == identity.project_id,
            Visitor.deleted_at.is_(None),
        )
        .populate_existing()
        .with_for_update()
        .first()
    )
    entry: VisitorWaitingQueue | None = (
        db.query(VisitorWaitingQueue)
        .filter(
            VisitorWaitingQueue.id == entry_id,
            VisitorWaitingQueue.project_id == identity.project_id,
            VisitorWaitingQueue.visitor_id == identity.visitor_id,
            VisitorWaitingQueue.status == "waiting",
            VisitorWaitingQueue.expired_at <= datetime.utcnow(),
        )
        .populate_existing()
        .with_for_update()
        .first()
    )
    if entry is None:
        return False
    entry.expire()
    schedule_queue_update(db, entry.project_id)
    platform = _close_current_wait(db, entry, visitor)
    if platform is None or visitor is None:
        return True
    notice = QueueTimeoutNotice(
        platform_id=platform.id,
        platform_type=platform.type,
        recipient=visitor.platform_open_id,
        external_status="not_required"
        if platform.type == "website"
        or visitor.platform_open_id == build_visitor_channel_id(visitor.id)
        else "pending",
    )
    entry.extra_metadata = {
        **(entry.extra_metadata or {}),
        QUEUE_TIMEOUT_NOTICE_KEY: notice.model_dump(mode="json"),
    }
    return True


def _close_current_wait(
    db: Session, entry: VisitorWaitingQueue, visitor: Visitor | None
) -> Platform | None:
    if visitor is None or visitor.service_status != "queued":
        return None

    # An old queue record must not end a newer conversation or newer wait.
    latest: VisitorSession | None = (
        db.query(VisitorSession)
        .filter(
            VisitorSession.visitor_id == visitor.id,
            VisitorSession.project_id == visitor.project_id,
        )
        .order_by(VisitorSession.created_at.desc(), VisitorSession.id.desc())
        .first()
    )
    if latest is not None and (
        latest.id != entry.session_id or latest.staff_id is not None
    ):
        return None
    another_wait = (
        db.query(VisitorWaitingQueue.id)
        .filter(
            VisitorWaitingQueue.visitor_id == visitor.id,
            VisitorWaitingQueue.project_id == visitor.project_id,
            VisitorWaitingQueue.status == "waiting",
            VisitorWaitingQueue.id != entry.id,
            VisitorWaitingQueue.entered_at >= entry.entered_at,
        )
        .first()
    )
    if another_wait is not None:
        return None

    platform: Platform | None = (
        db.query(Platform)
        .filter(
            Platform.id == visitor.platform_id,
            Platform.project_id == visitor.project_id,
        )
        .first()
    )
    visitor.service_status = "closed"
    visitor.updated_at = datetime.utcnow()
    if latest is not None and latest.status == "open":
        latest.close()
        latest.updated_at = datetime.utcnow()
    if platform is None or platform.deleted_at is not None:
        return None
    return platform


def cancel_entry(
    db: Session, entry_id: UUID, project_id: UUID, reason: str | None = None
) -> bool:
    identity = (
        db.query(VisitorWaitingQueue.visitor_id)
        .filter(
            VisitorWaitingQueue.id == entry_id,
            VisitorWaitingQueue.project_id == project_id,
        )
        .first()
    )
    if identity is None:
        return False
    visitor = (
        db.query(Visitor)
        .filter(
            Visitor.id == identity.visitor_id,
            Visitor.project_id == project_id,
            Visitor.deleted_at.is_(None),
        )
        .populate_existing()
        .with_for_update()
        .first()
    )
    entry = (
        db.query(VisitorWaitingQueue)
        .filter(
            VisitorWaitingQueue.id == entry_id,
            VisitorWaitingQueue.project_id == project_id,
            VisitorWaitingQueue.status == "waiting",
        )
        .populate_existing()
        .with_for_update()
        .first()
    )
    if entry is None:
        return False
    entry.cancel()
    if reason:
        entry.reason = f"{entry.reason or ''} | Cancelled: {reason}".strip(" |")[:255]
    _close_current_wait(db, entry, visitor)
    schedule_queue_update(db, project_id)
    return True
