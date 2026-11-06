"""Send committed handoff notices once and reconcile uncertain IM receipts."""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import String, cast, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import (
    ManualServiceRequest, Platform, Project, Staff, Visitor, VisitorSession,
)
from app.services.handoff_notice_state import (
    NOTICE_KEY, HandoffNotice, load_notice, save_notice,
    utc_now,
)
from app.services.handoff_notice_state import (
    stage_handoff_notice as stage_handoff_notice,
)
from app.services.queue_lifecycle import read_waiting_entry
from app.services.visitor_mutation_access import timestamp
from app.services.wukongim_client import wukongim_client

logger = get_logger(__name__)
__all__ = [
    "NOTICE_KEY", "HandoffNoticeSweep", "stage_handoff_notice",
    "deliver_handoff_notice", "recover_handoff_notices",
]


@dataclass(frozen=True)
class HandoffNoticeSweep:
    checked: int = 0
    sent: int = 0
    unknown: int = 0
    skipped: int = 0
    failed: int = 0
    next_cursor: UUID | None = None


def _live_channel(
    db: Session, request: ManualServiceRequest, visitor: Visitor | None,
    project: Project | None, notice: HandoffNotice,
) -> bool:
    if (
        visitor is None or project is None or project.deleted_at is not None
        or request.deleted_at is not None
        or request.status not in {"pending", "notified", "in_progress"}
        or visitor.deleted_at is not None
        or visitor.access_retired_at is not None
        or request.visitor_id != visitor.id
        or visitor.platform_id != notice.platform_id
        or request.channel_id != notice.channel_id
        or request.channel_type != notice.channel_type
    ):
        return False
    platform = db.query(Platform.id).filter(
        Platform.id == notice.platform_id,
        Platform.project_id == request.project_id,
        Platform.is_active.is_(True), Platform.deleted_at.is_(None),
    ).first()
    return platform is not None


def _current(
    db: Session, request: ManualServiceRequest, visitor: Visitor | None,
    project: Project | None, notice: HandoffNotice,
) -> bool:
    if not _live_channel(db, request, visitor, project, notice):
        return False
    if (
        visitor is None or visitor.service_mode != notice.mode
        or not visitor.ai_disabled or visitor.ai_disabled != notice.ai_disabled
        or notice.client_msg_no != f"handoff-{request.id}"
    ):
        return False
    latest = db.query(VisitorSession).filter(
        VisitorSession.project_id == request.project_id,
        VisitorSession.visitor_id == visitor.id,
    ).order_by(
        VisitorSession.created_at.desc(), VisitorSession.id.desc(),
    ).populate_existing().first()
    if (
        latest is None or latest.status != "open"
        or latest.platform_id != visitor.platform_id
    ):
        return False
    if notice.receipt_status == "active":
        target = db.query(Staff.id).filter(
            Staff.id == notice.assigned_staff_id,
            Staff.project_id == request.project_id,
            Staff.deleted_at.is_(None), Staff.account_enabled.is_(True),
            Staff.is_active.is_(True), Staff.role.in_(["admin", "user"]),
        ).first()
        return (
            visitor.service_status == "active" and target is not None
            and latest.id == notice.session_id
            and notice.assigned_staff_id is not None
            and latest.staff_id == notice.assigned_staff_id
        )
    return (
        notice.entry_id is not None
        and read_waiting_entry(db, visitor, notice.entry_id) is not None
    )


def _record(
    db: Session, request_id: UUID, notice: HandoffNotice,
    *, confirmed: bool, error: str | None,
) -> str:
    request = db.query(ManualServiceRequest).filter(
        ManualServiceRequest.id == request_id,
    ).populate_existing().with_for_update().first()
    current = load_notice(request) if request is not None else None
    if request is None or current is None:
        db.rollback()
        return "missing"
    if current.state in {"sent", "skipped"}:
        db.rollback()
        return current.state
    if (
        current.client_msg_no != notice.client_msg_no
        or current.channel_id != notice.channel_id
        or current.channel_type != notice.channel_type
        or current.payload() != notice.payload()
    ):
        db.rollback()
        return "unknown"
    current.state = "sent" if confirmed else "unknown"
    current.last_error = error
    current.updated_at = utc_now()
    save_notice(request, current)
    db.commit()
    return current.state


async def _reconcile(
    db: Session, request_id: UUID, notice: HandoffNotice,
) -> str:
    confirmed = False
    error = "IM_RECEIPT_UNKNOWN"
    try:
        message = await wukongim_client.get_message_by_client_msg_no(
            channel_id=notice.channel_id, channel_type=notice.channel_type,
            client_msg_no=notice.client_msg_no, raise_on_error=True,
        )
        if message is not None:
            confirmed = (
                message.client_msg_no == notice.client_msg_no
                and message.channel_id == notice.channel_id
                and message.channel_type == notice.channel_type
                and message.from_uid == "system"
                and message.payload == notice.payload()
            )
            error = "IM_MESSAGE_IDENTITY_CONFLICT"
    except Exception as exc:
        logger.warning(
            "Handoff notice lookup failed: %s", type(exc).__name__,
        )
        error = "IM_LOOKUP_FAILED"
    return _record(
        db, request_id, notice, confirmed=confirmed,
        error=None if confirmed else error,
    )


async def deliver_handoff_notice(db: Session, request_id: UUID) -> str:
    """Release locks before sending; reconcile ambiguous attempts."""
    identity = db.query(ManualServiceRequest).filter(
        ManualServiceRequest.id == request_id,
    ).populate_existing().first()
    if identity is None:
        db.rollback()
        return "missing"
    project = db.query(Project).filter(
        Project.id == identity.project_id,
    ).populate_existing().with_for_update(read=True).first()
    visitor = db.query(Visitor).filter(
        Visitor.id == identity.visitor_id,
        Visitor.project_id == identity.project_id,
    ).populate_existing().with_for_update(skip_locked=True).first()
    if identity.visitor_id is not None and visitor is None:
        db.rollback()
        return "busy"
    request = db.query(ManualServiceRequest).filter(
        ManualServiceRequest.id == request_id,
        ManualServiceRequest.project_id == identity.project_id,
    ).populate_existing().with_for_update(skip_locked=True).first()
    notice = load_notice(request) if request is not None else None
    if request is None or notice is None:
        db.rollback()
        return "missing"
    if notice.state in {"sent", "skipped"} or not wukongim_client.enabled:
        db.rollback()
        return notice.state
    if notice.state in {"sending", "unknown"}:
        if not _live_channel(db, request, visitor, project, notice):
            notice.state = "skipped"
            notice.last_error = "CHANNEL_UNAVAILABLE_AFTER_ATTEMPT"
            notice.updated_at = utc_now()
            save_notice(request, notice)
            db.commit()
            return "skipped"
        old_enough = timestamp(notice.updated_at) <= timestamp(
            utc_now() - timedelta(seconds=30)
        )
        db.commit()
        return await _reconcile(db, request_id, notice) if old_enough else (
            notice.state
        )
    if not _current(db, request, visitor, project, notice):
        notice.state = "skipped"
        notice.last_error = "RECEPTION_CHANGED"
        notice.updated_at = utc_now()
        save_notice(request, notice)
        db.commit()
        return "skipped"
    notice.state = "sending"
    notice.attempts += 1
    notice.updated_at = utc_now()
    save_notice(request, notice)
    db.commit()
    from app.services.human_handoff_service import _notify_handoff
    confirmed = False
    try:
        await _notify_handoff(
            content=notice.content, channel_id=notice.channel_id,
            channel_type=notice.channel_type, extra=notice.extra,
            client_msg_no=notice.client_msg_no,
        )
        confirmed = True
    except Exception as exc:
        logger.warning(
            "Handoff notice outcome unknown: %s", type(exc).__name__,
        )
    return _record(
        db, request_id, notice, confirmed=confirmed,
        error=None if confirmed else "IM_RECEIPT_UNKNOWN",
    )


async def recover_handoff_notices(
    db: Session, *, after_request_id: UUID | None = None, limit: int = 20,
) -> HandoffNoticeSweep:
    if limit < 1 or limit > 50:
        raise ValueError("Recovery limit must be between 1 and 50")
    if not wukongim_client.enabled:
        return HandoffNoticeSweep()
    query = select(ManualServiceRequest.id).where(
        cast(ManualServiceRequest.request_metadata[
            NOTICE_KEY
        ]["version"].as_string(), String) == "1",
        ManualServiceRequest.request_metadata[NOTICE_KEY]["state"].as_string(
        ).in_(["pending", "sending", "unknown"]),
    )
    if after_request_id is not None:
        query = query.where(ManualServiceRequest.id > after_request_id)
    ids = list(db.scalars(query.order_by(
        ManualServiceRequest.id,
    ).limit(limit)).all())
    if not ids and after_request_id is not None:
        return await recover_handoff_notices(db, limit=limit)
    sent = unknown = skipped = failed = 0
    for request_id in ids:
        try:
            status = await deliver_handoff_notice(db, request_id)
            sent += status == "sent"
            unknown += status in {"sending", "unknown"}
            skipped += status == "skipped"
        except Exception as exc:
            db.rollback()
            failed += 1
            logger.warning(
                "Handoff notice recovery failed: %s", type(exc).__name__,
            )
    return HandoffNoticeSweep(
        checked=len(ids), sent=sent, unknown=unknown, skipped=skipped,
        failed=failed, next_cursor=ids[-1] if ids else None,
    )
