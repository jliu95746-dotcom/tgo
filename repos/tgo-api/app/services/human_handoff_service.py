"""Persistent, channel-neutral human handoff orchestration."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
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
from app.services.visitor_notifications import notify_visitor_profile_updated
from app.services.wukongim_client import wukongim_client
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE, MessageType
from app.utils.encoding import build_visitor_channel_id
from app.utils.manual_service_tag import (
    MANUAL_SERVICE_TAG_ID,
    MANUAL_SERVICE_TAG_NAME,
    MANUAL_SERVICE_TAG_NAME_ZH,
)

OPEN_REQUEST_STATUSES = ("pending", "notified", "in_progress")


def ensure_manual_service_tag(
    db: Session,
    project_id: UUID,
    visitor: Visitor,
) -> None:
    """Ensure the visitor carries the shared manual-service tag."""
    tag = (
        db.query(Tag)
        .filter(Tag.id == MANUAL_SERVICE_TAG_ID, Tag.project_id == project_id)
        .first()
    )
    if tag is None:
        tag = Tag(
            id=MANUAL_SERVICE_TAG_ID,
            name=MANUAL_SERVICE_TAG_NAME,
            category=TagCategory.VISITOR,
            color="#3B82F6",
            project_id=project_id,
            name_zh=MANUAL_SERVICE_TAG_NAME_ZH,
            description="Flag visitors who requested human assistance",
        )
        db.add(tag)
    elif tag.deleted_at is not None:
        tag.deleted_at = None
        tag.updated_at = datetime.utcnow()

    visitor_tag = (
        db.query(VisitorTag)
        .filter(
            VisitorTag.visitor_id == visitor.id,
            VisitorTag.tag_id == MANUAL_SERVICE_TAG_ID,
        )
        .first()
    )
    if visitor_tag is None:
        db.add(
            VisitorTag(
                project_id=project_id,
                visitor_id=visitor.id,
                tag_id=MANUAL_SERVICE_TAG_ID,
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
) -> tuple[ManualServiceRequest, bool]:
    request = None
    if source_message_id:
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
    if request is None:
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
) -> None:
    await wukongim_client.send_system_message(
        channel_id=channel_id,
        channel_type=channel_type,
        content=content,
        msg_type=MessageType.HUMAN_HANDOFF_REQUESTED,
        from_uid="system",
        extra=extra,
        red_dot=True,
    )


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
) -> dict[str, Any]:
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
    )
    visitor.ai_disabled = True
    db.flush()

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
        db.commit()
        if not is_duplicate:
            await notify_visitor_profile_updated(db, visitor)
            await _notify_handoff(
                content="已收到您的人工客服请求，当前正在排队，请稍候。",
                channel_id=resolved_channel_id,
                channel_type=channel_type,
            )
        return {
            "request_id": str(handoff_request.id),
            "entry_id": str(existing_queue.id),
            "status": existing_queue.status,
            "position": existing_queue.position,
            "priority": existing_queue.priority,
            "channel_id": existing_queue.channel_id or resolved_channel_id,
            "channel_type": existing_queue.channel_type or channel_type,
            "message": "Visitor is waiting for human service",
        }

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
        db.commit()
        if not is_duplicate:
            await notify_visitor_profile_updated(db, visitor)
            await _notify_handoff(
                content="已收到您的人工客服请求，客服 {0} 将接手处理。",
                channel_id=resolved_channel_id,
                channel_type=channel_type,
                extra=[
                    {
                        "uid": f"{active_session.staff_id}-staff",
                        "name": staff_name,
                    }
                ],
            )
        return {
            "request_id": str(handoff_request.id),
            "assigned_staff_id": str(active_session.staff_id),
            "session_id": str(active_session.id),
            "status": VisitorServiceStatus.ACTIVE.value,
            "message": "Visitor is assigned to staff; AI replies are disabled",
        }

    if not visitor.is_unassigned:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Visitor cannot be handed off because no active staff session "
                f"or waiting queue exists (status: {visitor.service_status})"
            ),
        )

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
        db.commit()
        await notify_visitor_profile_updated(db, visitor)
        return {
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

    if transfer_result.waiting_queue is not None:
        queue = transfer_result.waiting_queue
        handoff_request.status = "pending"
        handoff_request.notification_type = "wukongim_queue"
        db.commit()
        await notify_visitor_profile_updated(db, visitor)
        await _notify_handoff(
            content="已收到您的人工客服请求，当前正在排队，请稍候。",
            channel_id=resolved_channel_id,
            channel_type=channel_type,
        )
        return {
            "request_id": str(handoff_request.id),
            "entry_id": str(queue.id),
            "status": queue.status,
            "position": queue.position,
            "priority": queue.priority,
            "channel_id": queue.channel_id or resolved_channel_id,
            "channel_type": queue.channel_type or channel_type,
            "message": transfer_result.message,
        }

    handoff_request.status = "rejected"
    db.commit()
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=(
            "Transfer reported success but created no staff session "
            "or waiting queue"
        ),
    )
