"""Deliver committed timeout intents without repeating uncertain channel sends."""

from datetime import datetime, timedelta
from uuid import UUID

import httpx
from pydantic import JsonValue, ValidationError
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import Platform, Visitor, VisitorSession, VisitorWaitingQueue
from app.schemas.queue_timeout import (
    QUEUE_TIMEOUT_NOTICE_KEY,
    QUEUE_TIMEOUT_TEXT,
    QueueTimeoutNotice,
)
from app.services.platform_message_client import forward_platform_message
from app.services.staff_message_target import PlatformMessageTarget
from app.services.wukongim_client import wukongim_client
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE, MessageType
from app.utils.encoding import build_visitor_channel_id

logger = get_logger("services.queue_timeout_delivery")
DEFINITE_REJECTIONS = {400, 401, 403, 404, 409, 413, 415, 422}


def _save(db: Session, entry: VisitorWaitingQueue, notice: QueueTimeoutNotice) -> None:
    entry.extra_metadata = {
        **(entry.extra_metadata or {}),
        QUEUE_TIMEOUT_NOTICE_KEY: notice.model_dump(mode="json"),
    }
    entry.updated_at = datetime.utcnow()
    db.commit()


def _current_target(
    db: Session, entry: VisitorWaitingQueue, notice: QueueTimeoutNotice
) -> tuple[PlatformMessageTarget | None, bool]:
    visitor: Visitor | None = (
        db.query(Visitor)
        .filter(
            Visitor.id == entry.visitor_id,
            Visitor.project_id == entry.project_id,
            Visitor.deleted_at.is_(None),
        )
        .first()
    )
    platform: Platform | None = (
        db.query(Platform)
        .filter(
            Platform.id == notice.platform_id,
            Platform.project_id == entry.project_id,
            Platform.deleted_at.is_(None),
        )
        .first()
    )
    if (
        visitor is None
        or platform is None
        or not platform.is_active
        or not platform.api_key
        or platform.type == "wechat_personal"
        or visitor.platform_id != notice.platform_id
        or visitor.platform_open_id != notice.recipient
        or platform.type != notice.platform_type
    ):
        return None, False
    latest = (
        db.query(VisitorSession.id)
        .filter(
            VisitorSession.visitor_id == visitor.id,
            VisitorSession.project_id == visitor.project_id,
        )
        .order_by(VisitorSession.created_at.desc(), VisitorSession.id.desc())
        .first()
    )
    current = visitor.service_status == "closed" and (
        latest is None or latest.id == entry.session_id
    )
    return (
        PlatformMessageTarget(
            project_id=entry.project_id,
            visitor_id=visitor.id,
            platform_id=platform.id,
            platform_type=platform.type,
            platform_api_key=platform.api_key,
            platform_open_id=notice.recipient,
            channel_id=build_visitor_channel_id(visitor.id),
            channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
        ),
        current,
    )


async def deliver_timeout_notice(db: Session, entry_id: UUID) -> None:
    """A stable per-entry identity is persisted before each network attempt.

    Unknown external outcomes are never resent. Unknown IM outcomes may only
    reconcile an existing matching history record, never resend on a miss.
    """
    entry: VisitorWaitingQueue | None = (
        db.query(VisitorWaitingQueue)
        .filter(
            VisitorWaitingQueue.id == entry_id, VisitorWaitingQueue.status == "expired"
        )
        .populate_existing()
        .with_for_update(skip_locked=True)
        .first()
    )
    if entry is None:
        db.rollback()
        return
    try:
        notice = QueueTimeoutNotice.model_validate(
            (entry.extra_metadata or {}).get(QUEUE_TIMEOUT_NOTICE_KEY)
        )
    except ValidationError:
        db.rollback()
        return
    if notice.next_retry_at > datetime.utcnow():
        db.rollback()
        return

    target, current = _current_target(db, entry, notice)
    if target is None:
        notice.history_error = "TARGET_UNAVAILABLE"
        notice.next_retry_at = datetime.utcnow() + timedelta(minutes=5)
        _save(db, entry, notice)
        return
    if not current:
        if notice.external_status == "pending":
            notice.external_status = "cancelled"
            notice.external_error = "CONVERSATION_CHANGED"
        if notice.history_status == "pending" and notice.external_status != "sent":
            notice.history_status = "cancelled"
            notice.history_error = "CONVERSATION_CHANGED"

    client_msg_no = f"queue-timeout-{entry.id.hex}"
    notice.next_retry_at = datetime.utcnow() + timedelta(minutes=5)
    if notice.external_status == "sending":
        notice.external_status = "unknown"
        notice.external_error = "EXTERNAL_RECEIPT_UNKNOWN"
    if notice.external_status == "pending":
        notice.external_status = "sending"
        notice.external_attempts += 1
        _save(db, entry, notice)
        try:
            response = await forward_platform_message(
                target,
                {"type": 1, "content": QUEUE_TIMEOUT_TEXT},
                client_msg_no,
                from_uid="system",
            )
            if 200 <= response.status_code < 300:
                body = response.json()
                notice.external_status = (
                    "sent"
                    if isinstance(body, dict) and body.get("ok") is True
                    else "unknown"
                )
            elif response.status_code in DEFINITE_REJECTIONS:
                notice.external_status = "failed"
            else:
                notice.external_status = "unknown"
            notice.external_error = (
                None
                if notice.external_status == "sent"
                else f"PLATFORM_HTTP_{response.status_code}"
            )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            notice.external_status = "pending"
            notice.external_error = type(exc).__name__
        except Exception as exc:
            notice.external_status = "unknown"
            notice.external_error = type(exc).__name__
        _save(db, entry, notice)

    if notice.external_status not in ("sent", "not_required"):
        _save(db, entry, notice)
        return
    payload: dict[str, JsonValue] = {
        "type": int(MessageType.QUEUE_TIMEOUT),
        "content": QUEUE_TIMEOUT_TEXT,
        "extra": [],
    }
    if notice.history_status in ("sending", "unknown"):
        try:
            existing = await wukongim_client.get_message_by_client_msg_no(
                channel_id=target.channel_id,
                channel_type=target.channel_type,
                client_msg_no=client_msg_no,
                raise_on_error=True,
            )
            if existing is not None:
                if existing.from_uid == "system" and existing.payload == payload:
                    notice.history_status = "sent"
                    notice.history_error = None
                else:
                    notice.history_status = "failed"
                    notice.history_error = "IM_IDENTITY_CONFLICT"
            else:
                notice.history_status = "unknown"
                notice.history_error = "IM_RECEIPT_UNKNOWN"
        except Exception as exc:
            notice.history_status = "unknown"
            notice.history_error = type(exc).__name__
        _save(db, entry, notice)
        return
    if notice.history_status != "pending":
        _save(db, entry, notice)
        return
    if not wukongim_client.enabled:
        notice.history_error = "IM_DISABLED"
        _save(db, entry, notice)
        return
    notice.history_status = "sending"
    notice.history_attempts += 1
    _save(db, entry, notice)
    try:
        history_response = await wukongim_client.send_message(
            from_uid="system",
            channel_id=target.channel_id,
            channel_type=target.channel_type,
            client_msg_no=client_msg_no,
            payload=payload,
        )
        notice.history_status = "sent" if history_response is not None else "unknown"
        notice.history_error = (
            None if history_response is not None else "IM_RECEIPT_MISSING"
        )
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
        notice.history_status = "pending"
        notice.history_error = type(exc).__name__
    except Exception as exc:
        notice.history_status = "unknown"
        notice.history_error = type(exc).__name__
    _save(db, entry, notice)


async def recover_timeout_notices(
    db: Session, *, limit: int = 20, project_id: UUID | None = None
) -> None:
    """Recover only persisted intents; never backfill notices for old expiries."""
    metadata = VisitorWaitingQueue.extra_metadata[QUEUE_TIMEOUT_NOTICE_KEY]
    external = metadata["external_status"].as_string()
    history = metadata["history_status"].as_string()
    query = db.query(VisitorWaitingQueue.id).filter(
        VisitorWaitingQueue.status == "expired",
        metadata["next_retry_at"].as_string() <= datetime.utcnow().isoformat(),
        or_(
            external.in_(["pending", "sending"]),
            external.in_(["sent", "not_required"])
            & history.in_(["pending", "sending", "unknown"]),
        ),
    )
    if project_id is not None:
        query = query.filter(VisitorWaitingQueue.project_id == project_id)
    ids = [
        row.id for row in query.order_by(VisitorWaitingQueue.updated_at).limit(limit)
    ]
    db.rollback()
    for entry_id in ids:
        try:
            await deliver_timeout_notice(db, entry_id)
        except Exception as exc:
            db.rollback()
            logger.warning(
                "Timeout notice recovery failed",
                extra={
                    "entry_id": str(entry_id),
                    "error_type": type(exc).__name__,
                },
            )
