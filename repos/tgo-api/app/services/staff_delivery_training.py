"""Capture assist corrections after delivery, without publishing skills."""

from datetime import timedelta
from uuid import UUID

from fastapi import HTTPException
from pydantic import JsonValue
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models.staff_message_delivery import StaffMessageDelivery, utc_now
from app.schemas.skill import (
    HumanizationTrainingSampleRequest,
    HumanizationTrainingStatus,
)
from app.schemas.staff_delivery import AssistTrainingIntent, StaffDeliveryRequest
from app.services.ai_client import ai_client
from app.services.staff_message_target import StaffMessageTarget


def training_snapshot(request: StaffDeliveryRequest) -> dict[str, JsonValue] | None:
    intent = request.training
    if intent is None:
        return None
    content = request.payload.get("content")
    if request.payload.get("type") not in (1, 12) or not isinstance(content, str):
        raise HTTPException(422, "只有带回复正文的人工修正可以训练拟人技能")
    if content.strip() == intent.ai_draft.strip():
        return None
    if not content.strip() or len(content) > 10000:
        raise HTTPException(422, "拟人训练回复正文必须为 1 到 10000 个字符")
    snapshot: dict[str, JsonValue] = intent.model_dump(mode="json")
    return snapshot


def authorize_training(
    target: StaffMessageTarget, snapshot: dict[str, JsonValue] | None
) -> None:
    if snapshot is None:
        return
    if (
        target.service_mode != "assist"
        or not target.humanization_skill_enabled
        or snapshot["skill_name"] != target.humanization_skill_name
    ):
        raise HTTPException(409, "拟人训练设置已变化，请刷新会话后重新确认发送内容")


async def _capture_locked(db: Session, row: StaffMessageDelivery) -> bool:
    try:
        intent = AssistTrainingIntent.model_validate(row.training_snapshot)
        content = row.payload.get("content")
        if not isinstance(content, str):
            raise ValueError("Training reply content is unavailable")
        sample = HumanizationTrainingSampleRequest(
            delivery_id=str(row.id),
            customer_message=intent.customer_message,
            ai_draft=intent.ai_draft,
            final_reply=content.strip(),
            source_message_id=intent.source_message_id,
            recent_messages=intent.recent_messages,
        )
        response = await ai_client.add_humanization_training_sample(
            str(row.project_id),
            intent.skill_name,
            sample.model_dump(mode="json", exclude_none=True),
        )
        status = HumanizationTrainingStatus.model_validate(response)
        if status.name != intent.skill_name:
            raise ValueError("Training receipt names a different skill")
        row.training_status = "saved"
        row.training_error = None
    except HTTPException as exc:
        # A deleted/incompatible skill must not silently recreate or retarget itself.
        if exc.status_code in (400, 404, 422):
            row.training_status = "unavailable"
        row.training_error = f"AI_HTTP_{exc.status_code}"
    except Exception as exc:
        # A lost AI response is retried using the immutable delivery id.
        row.training_error = type(exc).__name__[:100]
    row.training_attempts += 1
    row.training_next_retry_at = utc_now() + timedelta(
        seconds=min(300, 5 * 2 ** min(row.training_attempts, 6))
    )
    row.updated_at = utc_now()
    db.commit()
    return row.training_status == "saved"


async def recover_pending_training(
    db: Session, limit: int = 20, *, project_id: UUID | None = None
) -> int:
    eligible = and_(
        StaffMessageDelivery.training_status == "pending",
        StaffMessageDelivery.training_next_retry_at <= utc_now(),
        or_(
            StaffMessageDelivery.external_status == "sent",
            and_(
                StaffMessageDelivery.external_status == "not_required",
                StaffMessageDelivery.history_status == "sent",
            ),
        ),
    )
    query = db.query(StaffMessageDelivery.id).filter(eligible)
    if project_id is not None:
        query = query.filter(StaffMessageDelivery.project_id == project_id)
    ids = [
        item[0]
        for item in query.order_by(StaffMessageDelivery.training_next_retry_at)
        .limit(limit)
        .all()
    ]
    db.rollback()
    captured = 0
    for delivery_id in ids:
        row: StaffMessageDelivery | None = (
            db.query(StaffMessageDelivery)
            .filter(StaffMessageDelivery.id == delivery_id, eligible)
            .with_for_update(skip_locked=True)
            .first()
        )
        if row is None:
            db.rollback()
            continue
        captured += int(await _capture_locked(db, row))
    return captured
