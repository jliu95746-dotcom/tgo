"""Persistent, channel-neutral human handoff orchestration."""

from __future__ import annotations

import hashlib
import json
import logging
import unicodedata
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    AssignmentSource,
    ManualServiceRequest,
    Project,
    SessionStatus,
    Staff,
    Tag,
    TagCategory,
    Visitor,
    VisitorServiceStatus,
    VisitorSession,
    VisitorTag,
    VisitorWaitingQueue,
    WaitingStatus,
)
from app.services.transfer_service import transfer_to_staff
from app.services.handoff_dispatch_state import (
    PENDING_DISPATCH_KEY, completed_dispatch_receipt, stage_pending_dispatch,
)
from app.services.visitor_notifications import notify_visitor_profile_updated
from app.services.wukongim_client import wukongim_client
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE, MessageType
from app.utils.encoding import build_visitor_channel_id
from app.utils.manual_service_tag import (
    MANUAL_SERVICE_TAG_NAME,
    MANUAL_SERVICE_TAG_NAME_ZH,
    manual_service_tag_id,
)

OPEN_REQUEST_STATUSES = ("pending", "notified", "in_progress")
logger = logging.getLogger(__name__)
_EXPLICIT_HANDOFF_COMMANDS = frozenset({
    "转人工", "人工客服", "人工服务", "找人工",
    "我要转人工", "我要人工客服", "帮我转人工", "请帮我转人工",
})


def is_explicit_handoff_request(message: str) -> bool:
    """Recognize direct commands without routing quotes or conditional text."""
    normalized = unicodedata.normalize("NFKC", message).strip()
    normalized = normalized.strip(" \t\r\n!?.。，！？")
    normalized = "".join(normalized.split())
    return normalized in _EXPLICIT_HANDOFF_COMMANDS


def _handoff_action_digest(
    reason: str, channel: str | None, channel_id: str, channel_type: int,
) -> str:
    payload = json.dumps(
        [reason, channel, channel_id, channel_type], ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def ensure_manual_service_tag(
    db: Session,
    project_id: UUID,
    visitor: Visitor,
) -> None:
    """Ensure the visitor carries the shared manual-service tag."""
    tag = (
        db.query(Tag)
        .filter(
            Tag.project_id == project_id,
            Tag.name == MANUAL_SERVICE_TAG_NAME,
            Tag.category == TagCategory.VISITOR.value,
        )
        .first()
    )
    if tag is None:
        tag = Tag(
            name=MANUAL_SERVICE_TAG_NAME,
            category=TagCategory.VISITOR,
            color="#3B82F6",
            project_id=project_id,
            name_zh=MANUAL_SERVICE_TAG_NAME_ZH,
            description="Flag visitors who requested human assistance",
        )
        tag.id = manual_service_tag_id(project_id)
        db.add(tag)
    elif tag.deleted_at is not None:
        tag.deleted_at = None
        tag.updated_at = datetime.utcnow()

    visitor_tag = (
        db.query(VisitorTag)
        .filter(
            VisitorTag.visitor_id == visitor.id,
            VisitorTag.tag_id == tag.id,
        )
        .first()
    )
    if visitor_tag is None:
        db.add(
            VisitorTag(
                project_id=project_id,
                visitor_id=visitor.id,
                tag_id=tag.id,
            )
        )
    elif visitor_tag.deleted_at is not None:
        visitor_tag.deleted_at = None
        visitor_tag.updated_at = datetime.utcnow()


def _upsert_open_request(
    db: Session,
    *,
    project: Project,
    visitor: Visitor,
    reason: str,
    channel: str | None,
    channel_id: str,
    channel_type: int,
    source_message_id: str | None,
    routing_reason: str | None,
    strict_source_receipt: bool = False,
) -> tuple[ManualServiceRequest, bool]:
    request = None
    if source_message_id and not strict_source_receipt:
        request = (
            db.query(ManualServiceRequest)
            .filter(
                ManualServiceRequest.project_id == project.id,
                ManualServiceRequest.visitor_id == visitor.id,
                ManualServiceRequest.source_message_id == source_message_id,
                ManualServiceRequest.deleted_at.is_(None),
            )
            .first()
        )
    if request is None and not strict_source_receipt:
        request = (
            db.query(ManualServiceRequest)
            .filter(
                ManualServiceRequest.project_id == project.id,
                ManualServiceRequest.visitor_id == visitor.id,
                ManualServiceRequest.status.in_(OPEN_REQUEST_STATUSES),
                ManualServiceRequest.deleted_at.is_(None),
            )
            .order_by(ManualServiceRequest.created_at.desc())
            .first()
        )
    metadata: dict[str, object] = {
        "source_message_id": source_message_id,
        "routing_reason": routing_reason,
    }
    is_duplicate = bool(
        request is not None
        and source_message_id
        and (
            request.source_message_id == source_message_id
            or (
                isinstance(request.request_metadata, dict)
                and request.request_metadata.get("source_message_id")
                == source_message_id
            )
        )
    )
    if request is None:
        request = ManualServiceRequest(
            project_id=project.id,
            visitor_id=visitor.id,
            reason=reason,
            urgency="high",
            status="pending",
            channel=channel,
            channel_id=channel_id,
            channel_type=channel_type,
            source_message_id=source_message_id,
            routing_reason=routing_reason,
            request_metadata=metadata,
        )
        db.add(request)
    else:
        request.reason = reason
        request.channel = channel or request.channel
        request.channel_id = channel_id
        request.channel_type = channel_type
        request.source_message_id = (
            source_message_id or request.source_message_id
        )
        request.routing_reason = routing_reason or request.routing_reason
        request.request_metadata = {
            **(request.request_metadata or {}),
            **metadata,
        }
        request.updated_at = datetime.utcnow()
    return request, is_duplicate


async def _notify_handoff(
    *,
    content: str,
    channel_id: str,
    channel_type: int,
    extra: list[dict[str, str]] | None = None,
    client_msg_no: str | None = None,
) -> None:
    sent = await wukongim_client.send_system_message(
        channel_id=channel_id,
        channel_type=channel_type,
        content=content,
        msg_type=MessageType.HUMAN_HANDOFF_REQUESTED,
        from_uid="system",
        extra=extra,
        client_msg_no=client_msg_no,
        red_dot=True,
    )
    if sent is None:
        raise RuntimeError("WuKongIM returned no handoff message receipt")
    if client_msg_no and sent.client_msg_no != client_msg_no:
        raise RuntimeError(
            "WuKongIM returned a mismatched handoff message receipt"
        )


async def _finish_handoff(
    *,
    db: Session,
    request: ManualServiceRequest,
    visitor: Visitor,
    receipt: dict[str, object],
    action_digest: str | None,
    is_duplicate: bool,
    content: str,
    channel_id: str,
    channel_type: int,
    extra: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    metadata = dict(request.request_metadata or {})
    metadata.pop(PENDING_DISPATCH_KEY, None)
    request.request_metadata = metadata
    if action_digest is not None:
        request.request_metadata = {
            **(request.request_metadata or {}),
            "handoff_action_digest": action_digest,
            "handoff_receipt": receipt,
            "notification_status": "unknown",
        }
    db.commit()
    if is_duplicate:
        return {
            **receipt,
            "notification_status": (request.request_metadata or {}).get(
                "notification_status", "unknown"
            ),
        }
    try:
        await notify_visitor_profile_updated(db, visitor)
        await _notify_handoff(
            content=content, channel_id=channel_id,
            channel_type=channel_type, extra=extra,
            client_msg_no=(
                f"handoff-{request.id}" if action_digest is not None else None
            ),
        )
    except Exception as exc:
        logger.warning(
            "Committed handoff notification status unknown: %s",
            type(exc).__name__,
            extra={"request_id": str(request.id)},
        )
        return {**receipt, "notification_status": "unknown"}
    if action_digest is not None:
        request.request_metadata = {
            **(request.request_metadata or {}),
            "notification_status": "submitted",
        }
        try:
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning(
                "Handoff notification receipt status unknown: %s",
                type(exc).__name__,
                extra={"request_id": str(request.id)},
            )
            return {**receipt, "notification_status": "unknown"}
    return {**receipt, "notification_status": "submitted"}


def _saved_handoff_receipt(
    db: Session,
    project_id: UUID,
    visitor_id: UUID,
    source_message_id: str,
    action_digest: str,
) -> dict[str, object] | None:
    previous = (
        db.query(ManualServiceRequest)
        .filter(
            ManualServiceRequest.project_id == project_id,
            ManualServiceRequest.visitor_id == visitor_id,
            ManualServiceRequest.source_message_id == source_message_id,
        )
        .first()
    )
    if previous is None:
        return None
    metadata = previous.request_metadata or {}
    saved_digest = metadata.get("handoff_action_digest")
    if saved_digest is None:
        if previous.channel_id is None or previous.channel_type is None:
            raise HTTPException(409, "Saved handoff action is incomplete")
        saved_digest = _handoff_action_digest(
            previous.reason,
            previous.channel,
            previous.channel_id,
            previous.channel_type,
        )
    if (
        previous.deleted_at is not None
        or saved_digest != action_digest
    ):
        raise HTTPException(
            409, "Source handoff request conflicts with saved action",
        )
    saved_receipt = metadata.get("handoff_receipt")
    if isinstance(saved_receipt, dict):
        return {
            **saved_receipt,
            "notification_status": metadata.get(
                "notification_status", "unknown",
            ),
        }
    return {
        "request_id": str(previous.id),
        "status": "unknown",
        "notification_status": "unknown",
        "message": "Handoff outcome requires manual review",
    }


async def request_human_handoff(
    *,
    db: Session,
    project: Project,
    visitor: Visitor,
    reason: str,
    source_message_id: str | None = None,
    routing_reason: str | None = None,
    channel: str | None = None,
    channel_id: str | None = None,
    channel_type: int = CHANNEL_TYPE_CUSTOMER_SERVICE,
    strict_source_receipt: bool = False,
) -> dict[str, object]:
    """Persist one human-handoff request across all chat channels."""
    normalized_reason = reason.strip()
    if not normalized_reason:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Manual service request reason cannot be empty",
        )
    if visitor.project_id != project.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Visitor does not belong to the specified project",
        )

    resolved_channel_id = channel_id or build_visitor_channel_id(visitor.id)
    action_digest: str | None = None
    if strict_source_receipt:
        if not source_message_id or len(source_message_id) > 255:
            raise HTTPException(400, "A valid source_message_id is required")
        action_digest = _handoff_action_digest(
            normalized_reason, channel, resolved_channel_id, channel_type,
        )
        previous_receipt = _saved_handoff_receipt(
            db, project.id, visitor.id, source_message_id, action_digest,
        )
        if previous_receipt is not None:
            return previous_receipt
    ensure_manual_service_tag(db, project.id, visitor)
    handoff_request, is_duplicate = _upsert_open_request(
        db,
        project=project,
        visitor=visitor,
        reason=normalized_reason,
        channel=channel,
        channel_id=resolved_channel_id,
        channel_type=channel_type,
        source_message_id=source_message_id,
        routing_reason=routing_reason,
        strict_source_receipt=strict_source_receipt,
    )
    visitor.ai_disabled = True
    visitor.service_mode = "manual"
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        if strict_source_receipt and source_message_id and action_digest:
            previous_receipt = _saved_handoff_receipt(
                db, project.id, visitor.id, source_message_id, action_digest,
            )
            if previous_receipt is not None:
                return previous_receipt
        raise
    from app.core.config import settings
    if settings.CUSTOMER_TURNS_ENABLED:
        from app.services.customer_turn_ingress import cancel_customer_turns
        await cancel_customer_turns(db, visitor)

    existing_queue = (
        db.query(VisitorWaitingQueue)
        .filter(
            VisitorWaitingQueue.visitor_id == visitor.id,
            VisitorWaitingQueue.project_id == project.id,
            VisitorWaitingQueue.status == WaitingStatus.WAITING.value,
        )
        .first()
    )
    if existing_queue is not None:
        handoff_request.status = "pending"
        handoff_request.notification_type = "wukongim_queue"
        receipt = {
            "request_id": str(handoff_request.id),
            "entry_id": str(existing_queue.id),
            "status": existing_queue.status,
            "position": existing_queue.position,
            "priority": existing_queue.priority,
            "channel_id": existing_queue.channel_id or resolved_channel_id,
            "channel_type": existing_queue.channel_type or channel_type,
            "message": "Visitor is waiting for human service",
        }
        return await _finish_handoff(
            db=db, request=handoff_request, visitor=visitor, receipt=receipt,
            action_digest=action_digest, is_duplicate=is_duplicate,
            content="已收到您的人工客服请求，当前正在排队，请稍候。",
            channel_id=resolved_channel_id, channel_type=channel_type,
        )

    active_session = (
        db.query(VisitorSession)
        .filter(
            VisitorSession.visitor_id == visitor.id,
            VisitorSession.status == SessionStatus.OPEN.value,
            VisitorSession.staff_id.isnot(None),
        )
        .first()
    )
    if active_session is not None and active_session.staff_id is not None:
        staff = (
            db.query(Staff)
            .filter(Staff.id == active_session.staff_id)
            .first()
        )
        staff_name = (
            (staff.name or staff.nickname or staff.username)
            if staff is not None
            else "人工客服"
        )
        handoff_request.status = "in_progress"
        handoff_request.notification_type = "wukongim_red_dot"
        receipt = {
            "request_id": str(handoff_request.id),
            "assigned_staff_id": str(active_session.staff_id),
            "session_id": str(active_session.id),
            "status": VisitorServiceStatus.ACTIVE.value,
            "message": "Visitor is assigned to staff; AI replies are disabled",
        }
        return await _finish_handoff(
            db=db, request=handoff_request, visitor=visitor, receipt=receipt,
            action_digest=action_digest, is_duplicate=is_duplicate,
            content="已收到您的人工客服请求，客服 {0} 将接手处理。",
            channel_id=resolved_channel_id, channel_type=channel_type,
            extra=[{
                "uid": f"{active_session.staff_id}-staff",
                "name": staff_name,
            }],
        )

    if not visitor.is_unassigned:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Visitor cannot be handed off because no active staff session "
                f"or waiting queue exists (status: {visitor.service_status})"
            ),
        )

    # Stop automatic replies durably, and release this request's visitor writes
    # before the potentially slow dispatcher. Final assignment is a new stage.
    handoff_request.status = "pending"
    stage_pending_dispatch(db, handoff_request, visitor)
    db.commit()
    transfer_result = await transfer_to_staff(
        db=db,
        visitor_id=visitor.id,
        project_id=project.id,
        source=AssignmentSource.RULE,
        visitor_message=normalized_reason,
        platform_id=getattr(visitor, "platform_id", None),
        add_to_queue_if_no_staff=True,
        ai_disabled=True,
    )
    if not transfer_result.success:
        # Recovery may have queued/confirmed this request while the old model
        # was outstanding. Never overwrite its committed outcome as rejected.
        db.rollback()
        recovered = completed_dispatch_receipt(db, handoff_request.id)
        if recovered is not None:
            return recovered
        handoff_request.status = "rejected"
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to transfer visitor to staff: "
                f"{transfer_result.message}"
            ),
        )

    if transfer_result.assigned_staff_id is not None:
        handoff_request.status = "in_progress"
        handoff_request.notification_type = "wukongim_red_dot"
        receipt = {
            "request_id": str(handoff_request.id),
            "assigned_staff_id": str(transfer_result.assigned_staff_id),
            "session_id": (
                str(transfer_result.session.id)
                if transfer_result.session is not None
                else None
            ),
            "status": VisitorServiceStatus.ACTIVE.value,
            "message": transfer_result.message,
        }
        return await _finish_handoff(
            db=db, request=handoff_request, visitor=visitor, receipt=receipt,
            action_digest=action_digest, is_duplicate=is_duplicate,
            content="已收到您的人工客服请求，人工客服将接手处理。",
            channel_id=resolved_channel_id, channel_type=channel_type,
        )

    if transfer_result.waiting_queue is not None:
        queue = transfer_result.waiting_queue
        handoff_request.status = "pending"
        handoff_request.notification_type = "wukongim_queue"
        receipt = {
            "request_id": str(handoff_request.id),
            "entry_id": str(queue.id),
            "status": queue.status,
            "position": queue.position,
            "priority": queue.priority,
            "channel_id": queue.channel_id or resolved_channel_id,
            "channel_type": queue.channel_type or channel_type,
            "message": transfer_result.message,
        }
        return await _finish_handoff(
            db=db, request=handoff_request, visitor=visitor, receipt=receipt,
            action_digest=action_digest, is_duplicate=is_duplicate,
            content="已收到您的人工客服请求，当前正在排队，请稍候。",
            channel_id=resolved_channel_id, channel_type=channel_type,
        )

    handoff_request.status = "rejected"
    db.commit()
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=(
            "Transfer reported success but created no staff session "
            "or waiting queue"
        ),
    )
