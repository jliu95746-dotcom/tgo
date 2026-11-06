"""Recover accepted handoffs through the existing durable waiting queue."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import String, cast, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.core.config import settings
from app.core.logging import get_logger
from app.models import (
    ManualServiceRequest,
    Platform,
    Project,
    Visitor,
    VisitorSession,
    VisitorWaitingQueue,
)
from app.services.handoff_dispatch_state import (
    PENDING_DISPATCH_KEY, dispatch_binding,
)
from app.services.queue_lifecycle import read_waiting_entry
from app.services.staff_assignment_guard import read_assignment_rule
from app.services.transfer_service import (
    _add_to_waiting_queue, _get_or_create_session,
)
from app.services.visitor_mutation_access import timestamp

logger = get_logger(__name__)
RECOVERY_DELAY_SECONDS = 300


@dataclass(frozen=True)
class HandoffRecoverySweep:
    checked: int = 0
    recovered: int = 0
    cancelled: int = 0
    failed: int = 0
    next_cursor: UUID | None = None


def _pending(cutoff: datetime) -> tuple[ColumnElement[bool], ...]:
    return (
        ManualServiceRequest.status == "pending",
        ManualServiceRequest.deleted_at.is_(None),
        ManualServiceRequest.updated_at <= cutoff,
        ManualServiceRequest.request_metadata[
            PENDING_DISPATCH_KEY
        ].as_string().is_not(None),
        cast(ManualServiceRequest.request_metadata[
            PENDING_DISPATCH_KEY
        ]["version"].as_string(), String) == "1",
    )


def _finish(
    db: Session, request: ManualServiceRequest, receipt: dict[str, object],
    *, cancelled: bool = False,
) -> str:
    metadata = dict(request.request_metadata or {})
    metadata.pop(PENDING_DISPATCH_KEY, None)
    request.request_metadata = {
        **metadata, "handoff_receipt": receipt,
        "notification_status": "unknown",
    }
    request.status = (
        "cancelled" if cancelled else
        "in_progress" if receipt["status"] == "active" else "pending"
    )
    if not cancelled:
        request.notification_type = (
            "wukongim_red_dot" if receipt["status"] == "active"
            else "wukongim_queue"
        )
    db.commit()
    return "cancelled" if cancelled else "recovered"


async def _recover_one(
    db: Session, request_id: UUID, cutoff: datetime,
) -> str:
    identity = db.execute(select(
        ManualServiceRequest.project_id, ManualServiceRequest.visitor_id,
    ).where(ManualServiceRequest.id == request_id)).first()
    if identity is None or identity.visitor_id is None:
        return "skipped"
    # Keep the same company -> project -> visitor -> resources lock order.
    if settings.SAAS_BILLING_ENABLED:
        from app.services.company_membership import lock_company
        lock_company(db, identity.project_id)
    project = db.query(Project).filter(
        Project.id == identity.project_id,
    ).populate_existing().with_for_update(read=True).first()
    visitor = db.query(Visitor).filter(
        Visitor.id == identity.visitor_id,
        Visitor.project_id == identity.project_id,
    ).populate_existing().with_for_update(skip_locked=True).first()
    if project is None or visitor is None:
        db.rollback()
        return "skipped"
    request = db.query(ManualServiceRequest).filter(
        ManualServiceRequest.id == request_id, *_pending(cutoff),
        ManualServiceRequest.project_id == project.id,
        ManualServiceRequest.visitor_id == visitor.id,
    ).populate_existing().with_for_update(skip_locked=True).first()
    if request is None:
        db.rollback()
        return "skipped"
    metadata = request.request_metadata or {}
    marker = metadata.get(PENDING_DISPATCH_KEY)
    cancelled: dict[str, object] = {
        "request_id": str(request.id), "status": "cancelled",
        "message": "Reception changed; pending handoff was not repeated",
    }
    if (
        not isinstance(marker, dict) or marker.get("version") != 1
        or project.deleted_at is not None or visitor.deleted_at is not None
        or visitor.access_retired_at is not None
        or marker.get("platform_id") != str(visitor.platform_id)
        or visitor.service_mode != "manual" or not visitor.ai_disabled
    ):
        return _finish(db, request, cancelled, cancelled=True)
    platform = db.query(Platform.id).filter(
        Platform.id == visitor.platform_id,
        Platform.project_id == project.id,
        Platform.is_active.is_(True), Platform.deleted_at.is_(None),
    ).with_for_update(read=True).first()
    if platform is None:
        return _finish(db, request, cancelled, cancelled=True)
    current = db.query(VisitorSession).filter(
        VisitorSession.project_id == project.id,
        VisitorSession.visitor_id == visitor.id,
    ).order_by(
        VisitorSession.created_at.desc(), VisitorSession.id.desc(),
    ).populate_existing().with_for_update().first()
    receipt: dict[str, object] = {"request_id": str(request.id)}
    if (
        visitor.service_status == "active" and current is not None
        and current.status == "open" and current.staff_id is not None
        and current.platform_id == visitor.platform_id
    ):
        receipt.update({
            "status": "active", "session_id": str(current.id),
            "assigned_staff_id": str(current.staff_id),
            "message": "Existing human reception confirmed",
        })
        return _finish(db, request, receipt)
    entry: VisitorWaitingQueue | None = None
    if (
        visitor.service_status == "queued" and current is not None
        and current.status == "open" and current.staff_id is None
        and current.platform_id == visitor.platform_id
    ):
        candidate = db.query(VisitorWaitingQueue.id).filter(
            VisitorWaitingQueue.project_id == project.id,
            VisitorWaitingQueue.visitor_id == visitor.id,
            VisitorWaitingQueue.status == "waiting",
        ).order_by(VisitorWaitingQueue.entered_at.desc()).first()
        if candidate is not None:
            entry = read_waiting_entry(db, visitor, candidate.id, lock=True)
    elif visitor.is_unassigned and dispatch_binding(db, visitor) == marker:
        if settings.SAAS_BILLING_ENABLED:
            from app.services.company_entitlements import require_human_service
            require_human_service(db, project.id, visitor.id)
        session = await _get_or_create_session(
            db, visitor.id, project.id, platform_id=visitor.platform_id,
        )
        if (
            current is not None and session.id != current.id
            and timestamp(session.created_at) <= timestamp(current.created_at)
        ):
            # Stable latest-session ordering also matters with equal DB times.
            session.created_at = (
                timestamp(current.created_at) + timedelta(microseconds=1)
            )
        entry, _position = await _add_to_waiting_queue(
            db, project.id, visitor.id, visitor, session.id,
            request.reason, "Recover accepted human handoff",
            read_assignment_rule(db, project.id, lock=True), True,
        )
        entry.channel_id = request.channel_id
        entry.channel_type = request.channel_type
        db.flush()
    if entry is None:
        return _finish(db, request, cancelled, cancelled=True)
    receipt.update({
        "entry_id": str(entry.id), "status": "waiting",
        "position": entry.position, "priority": entry.priority,
        "channel_id": entry.channel_id or request.channel_id,
        "channel_type": entry.channel_type or request.channel_type,
        "message": "Visitor is waiting for human service",
    })
    return _finish(db, request, receipt)


async def recover_pending_handoffs(
    db: Session, *, after_request_id: UUID | None = None, limit: int = 20,
) -> HandoffRecoverySweep:
    """Visit a fair bounded page; never repeat an external model or notice."""
    if limit < 1 or limit > 50:
        raise ValueError("Recovery limit must be between 1 and 50")
    cutoff = datetime.utcnow() - timedelta(seconds=RECOVERY_DELAY_SECONDS)
    query = select(ManualServiceRequest.id).where(*_pending(cutoff))
    if after_request_id is not None:
        query = query.where(ManualServiceRequest.id > after_request_id)
    ids = list(db.scalars(query.order_by(
        ManualServiceRequest.id,
    ).limit(limit)).all())
    if not ids and after_request_id is not None:
        return await recover_pending_handoffs(db, limit=limit)
    recovered = cancelled = failed = 0
    for request_id in ids:
        try:
            outcome = await _recover_one(db, request_id, cutoff)
            recovered += outcome == "recovered"
            cancelled += outcome == "cancelled"
        except Exception as exc:
            db.rollback()
            failed += 1
            logger.warning("Handoff recovery failed: %s", type(exc).__name__)
    return HandoffRecoverySweep(
        checked=len(ids), recovered=recovered, cancelled=cancelled,
        failed=failed, next_cursor=ids[-1] if ids else None,
    )
