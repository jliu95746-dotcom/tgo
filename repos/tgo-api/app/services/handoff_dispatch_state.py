"""Versioned, local-only recovery state for an accepted human request."""

from uuid import UUID

from sqlalchemy.orm import Session

from app.models import ManualServiceRequest, Visitor, VisitorSession

PENDING_DISPATCH_KEY = "handoff_dispatch_pending"


def dispatch_binding(db: Session, visitor: Visitor) -> dict[str, object]:
    session = (
        db.query(VisitorSession)
        .filter(
            VisitorSession.project_id == visitor.project_id,
            VisitorSession.visitor_id == visitor.id,
        )
        .order_by(VisitorSession.created_at.desc(), VisitorSession.id.desc())
        .first()
    )
    return {
        "version": 1,
        "platform_id": str(visitor.platform_id),
        "status": visitor.service_status,
        "mode": visitor.service_mode,
        "ai_disabled": visitor.ai_disabled,
        "session_id": str(session.id) if session is not None else None,
        "session_status": session.status if session is not None else None,
        "session_staff_id": (
            str(session.staff_id)
            if session is not None and session.staff_id is not None else None
        ),
    }


def stage_pending_dispatch(
    db: Session, request: ManualServiceRequest, visitor: Visitor,
) -> None:
    request.request_metadata = {
        **(request.request_metadata or {}),
        PENDING_DISPATCH_KEY: dispatch_binding(db, visitor),
    }


def completed_dispatch_receipt(
    db: Session, request_id: UUID,
) -> dict[str, object] | None:
    """Recovery may have finished after the old AI was discarded."""
    request = (
        db.query(ManualServiceRequest)
        .filter(ManualServiceRequest.id == request_id)
        .populate_existing()
        .first()
    )
    if request is None or request.deleted_at is not None:
        return None
    metadata = request.request_metadata or {}
    receipt = metadata.get("handoff_receipt")
    if PENDING_DISPATCH_KEY in metadata or not isinstance(receipt, dict):
        return None
    return {
        **receipt,
        "notification_status": metadata.get("notification_status", "unknown"),
    }
