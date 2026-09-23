"""Durable channel acceptance followed by independently retried IM history.

Only definitive pre-delivery failures may be explicitly retried. Timeouts and
interrupted external attempts stay unknown: recovery never resends externally.
"""

import hashlib
import json
import re
from datetime import timedelta, timezone
from typing import Literal
from uuid import UUID

import httpx
from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.staff_message_delivery import StaffMessageDelivery, utc_now
from app.schemas.staff_delivery import (
    AssistTrainingIntent,
    StaffDeliveryRecord,
    StaffDeliveryRequest,
    StaffDeliveryResponse,
)
from app.services.platform_message_client import forward_staff_platform_message
from app.services.staff_delivery_training import authorize_training, training_snapshot
from app.services.employee_style import resolve_employee_style
from app.services.staff_message_target import StaffMessageTarget
from app.services.wukongim_client import wukongim_client

UNKNOWN_MESSAGE = "发送结果暂时无法确认，请先确认客户是否收到，勿重复发送。"
DEFINITE_REJECTIONS = {400, 401, 403, 404, 409, 413, 415, 422}


def receipt(row: StaffMessageDelivery) -> StaffDeliveryResponse:
    training_status: Literal["none", "pending", "saved", "unavailable"] = "none"
    if row.training_status == "pending":
        training_status = "pending"
    elif row.training_status == "saved":
        training_status = "saved"
    elif row.training_status == "unavailable":
        training_status = "unavailable"
    training_skill = (row.training_snapshot or {}).get("skill_name")
    delivery_status: Literal[
        "processing", "pending", "sent", "failed", "unknown"
    ] = "pending"
    if row.external_status == "sent" or (
        row.external_status == "not_required" and row.history_status == "sent"
    ):
        delivery_status = "sent"
    elif row.external_status == "failed":
        delivery_status = "failed"
    elif row.external_status == "unknown":
        delivery_status = "unknown"
    elif row.external_status == "sending":
        started = (
            row.updated_at.replace(tzinfo=timezone.utc)
            if row.updated_at.tzinfo is None
            else row.updated_at
        )
        delivery_status = (
            "processing" if utc_now() - started < timedelta(minutes=5) else "unknown"
        )
    return StaffDeliveryResponse(
        training_status=training_status,
        training_skill_name=training_skill if isinstance(training_skill, str) else None,
        client_msg_no=row.client_msg_no,
        delivery_status=delivery_status,
        history_status="sent" if row.history_status == "sent" else "pending",
        error_code=row.error_code,
        error_message=UNKNOWN_MESSAGE
        if delivery_status == "unknown"
        else row.error_message,
    )


def get_delivery(
    db: Session, target: StaffMessageTarget, client_msg_no: str
) -> StaffMessageDelivery:
    row: StaffMessageDelivery | None = (
        db.query(StaffMessageDelivery)
        .filter(
            StaffMessageDelivery.project_id == target.project_id,
            StaffMessageDelivery.staff_id == target.staff_id,
            StaffMessageDelivery.channel_id == target.channel_id,
            StaffMessageDelivery.channel_type == target.channel_type,
            StaffMessageDelivery.client_msg_no == client_msg_no,
        )
        .first()
    )
    if row is None:
        raise HTTPException(404, "未找到该消息的发送记录")
    return row


def pending_deliveries(
    db: Session,
    target: StaffMessageTarget,
    limit: int = 100,
    after: str = "",
) -> list[StaffDeliveryRecord]:
    rows: list[StaffMessageDelivery] = (
        db.query(StaffMessageDelivery)
        .filter(
            StaffMessageDelivery.project_id == target.project_id,
            StaffMessageDelivery.staff_id == target.staff_id,
            StaffMessageDelivery.channel_id == target.channel_id,
            StaffMessageDelivery.channel_type == target.channel_type,
            or_(
                StaffMessageDelivery.history_status == "pending",
                StaffMessageDelivery.training_status.in_(["pending", "unavailable"]),
            ),
            StaffMessageDelivery.client_msg_no > after,
        )
        .order_by(StaffMessageDelivery.client_msg_no)
        .limit(limit)
        .all()
    )
    return [
        StaffDeliveryRecord(
            request=StaffDeliveryRequest(
                channel_id=row.channel_id,
                channel_type=251,
                client_msg_no=row.client_msg_no,
                payload=row.payload,
                training=AssistTrainingIntent.model_validate(row.training_snapshot)
                if row.training_snapshot
                else None,
            ),
            receipt=receipt(row),
            created_at=row.created_at,
        )
        for row in rows
    ]


def _claim(
    db: Session, target: StaffMessageTarget, request: StaffDeliveryRequest
) -> tuple[StaffMessageDelivery, bool]:
    snapshot = training_snapshot(request)
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "channel_id": target.channel_id,
                "channel_type": target.channel_type,
                "platform_id": str(target.platform_id),
                "recipient": target.platform_open_id,
                "payload": request.payload,
                **({"training": snapshot} if snapshot else {}),
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()

    def existing() -> StaffMessageDelivery | None:
        found: StaffMessageDelivery | None = (
            db.query(StaffMessageDelivery)
            .filter(
                StaffMessageDelivery.project_id == target.project_id,
                StaffMessageDelivery.staff_id == target.staff_id,
                StaffMessageDelivery.client_msg_no == request.client_msg_no,
            )
            .with_for_update()
            .first()
        )
        return found

    row = existing()
    if row is None:
        authorize_training(target, snapshot)
        row = StaffMessageDelivery(
            project_id=target.project_id,
            staff_id=target.staff_id,
            visitor_id=target.visitor_id,
            channel_id=target.channel_id,
            channel_type=target.channel_type,
            client_msg_no=request.client_msg_no,
            request_fingerprint=fingerprint,
            payload=request.payload,
            training_snapshot=snapshot,
            training_status="pending" if snapshot else "none",
            external_status="sending"
            if target.requires_external_delivery
            else "not_required",
        )
        db.add(row)
        try:
            db.commit()
            return row, True
        except IntegrityError:
            db.rollback()
            row = existing()
            if row is None:
                raise
    if row.request_fingerprint != fingerprint:
        db.rollback()
        raise HTTPException(409, "相同消息编号对应的内容或收件人不一致，请勿复用消息编号。")
    if row.external_status == "failed" and request.retry_failed:
        row.external_status = "sending"
        row.error_code = None
        row.error_message = None
        row.updated_at = utc_now()
        db.commit()
        return row, True
    db.commit()
    return row, False


async def write_delivery_history(row: StaffMessageDelivery) -> None:
    """Use the same server IM client as AI; never call any channel adapter here."""
    if not wukongim_client.enabled:
        raise RuntimeError("IM_DISABLED")
    from_uid = f"{row.staff_id}-staff"
    # A prior IM attempt may have succeeded before its HTTP response was lost.
    # Reconcile by the original message identity before an at-least-once retry.
    if row.history_attempts:
        existing = await wukongim_client.get_message_by_client_msg_no(
            channel_id=row.channel_id,
            channel_type=row.channel_type,
            client_msg_no=row.client_msg_no,
            raise_on_error=True,
        )
        if existing is not None:
            if existing.from_uid != from_uid or existing.payload != row.payload:
                raise RuntimeError("IM_MESSAGE_IDENTITY_CONFLICT")
            return
    response = await wukongim_client.send_message(
        from_uid=from_uid,
        channel_id=row.channel_id,
        channel_type=row.channel_type,
        client_msg_no=row.client_msg_no,
        payload=row.payload,
    )
    if response is None:
        raise RuntimeError("IM_RECEIPT_MISSING")


async def _sync_history_locked(db: Session, row: StaffMessageDelivery) -> None:
    try:
        await write_delivery_history(row)
        row.history_status = "sent"
        row.history_error = None
    except Exception as exc:
        # Persist the intent before channel delivery; even a restart retains it.
        row.history_error = type(exc).__name__[:100]
        row.next_retry_at = utc_now() + timedelta(
            seconds=min(300, 5 * 2 ** min(row.history_attempts, 6))
        )
    row.history_attempts += 1
    row.updated_at = utc_now()
    db.commit()


async def sync_delivery_history(db: Session, delivery_id: UUID) -> None:
    row = (
        db.query(StaffMessageDelivery)
        .filter(
            StaffMessageDelivery.id == delivery_id,
            StaffMessageDelivery.external_status.in_(["sent", "not_required"]),
            StaffMessageDelivery.history_status == "pending",
        )
        .with_for_update(skip_locked=True)
        .first()
    )
    if row is not None:
        await _sync_history_locked(db, row)
    else:
        db.rollback()


async def recover_pending_history(
    db: Session,
    limit: int = 20,
    *,
    project_id: UUID | None = None,
) -> int:
    """Skip locked rows so API workers cannot replay the same pending record."""
    recovered = 0
    # Select IDs first; each attempt commits independently rather than holding a
    # batch of row locks while waiting on the IM service.
    query = db.query(StaffMessageDelivery.id).filter(
        StaffMessageDelivery.external_status.in_(["sent", "not_required"]),
        StaffMessageDelivery.history_status == "pending",
        StaffMessageDelivery.next_retry_at <= utc_now(),
    )
    if project_id is not None:
        query = query.filter(StaffMessageDelivery.project_id == project_id)
    ids = [
        item[0]
        for item in query.order_by(StaffMessageDelivery.next_retry_at)
        .limit(limit)
        .all()
    ]
    db.rollback()
    for delivery_id in ids:
        row = (
            db.query(StaffMessageDelivery)
            .filter(
                StaffMessageDelivery.id == delivery_id,
                StaffMessageDelivery.external_status.in_(["sent", "not_required"]),
                StaffMessageDelivery.history_status == "pending",
                StaffMessageDelivery.next_retry_at <= utc_now(),
            )
            .with_for_update(skip_locked=True)
            .first()
        )
        if row is None:
            db.rollback()
            continue
        await _sync_history_locked(db, row)
        if row.history_status == "sent":
            recovered += 1
    return recovered


def _rejection(response: httpx.Response, target: StaffMessageTarget) -> tuple[str, str]:
    code = f"PLATFORM_HTTP_{response.status_code}"
    message = "渠道未接受这条消息，请检查渠道配置与消息内容后重试。"
    try:
        body = response.json()
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict):
            if isinstance(error.get("code"), str):
                code = error["code"][:80]
            if isinstance(error.get("message"), str):
                message = error["message"][:500]
    except ValueError:
        pass
    message = re.sub(r"https?://\S+", "[地址已隐藏]", message)
    return code, message.replace(target.platform_api_key, "[已隐藏]")


async def deliver(
    db: Session, target: StaffMessageTarget, request: StaffDeliveryRequest
) -> StaffDeliveryResponse:
    from app.services.company_entitlements import require_human_service
    require_human_service(db, target.project_id, target.visitor_id)
    if target.platform_type == "wechat_personal":
        raise HTTPException(410, "该渠道已下线，无法发送消息")
    if (request.channel_id, request.channel_type) != (
        target.channel_id,
        target.channel_type,
    ):
        raise HTTPException(400, "发送目标与已授权会话不一致")
    needs_employee_style = (
        target.service_mode == "assist" and not target.humanization_skill_name
        and training_snapshot(request) is not None
    )
    # A retry uses the immutable saved intent; it must not depend on today's
    # employee setting or AI service availability to recover its delivery status.
    existing_delivery = db.query(StaffMessageDelivery.id).filter(
        StaffMessageDelivery.project_id == target.project_id,
        StaffMessageDelivery.staff_id == target.staff_id,
        StaffMessageDelivery.client_msg_no == request.client_msg_no,
    ).first() if needs_employee_style else None
    if needs_employee_style and existing_delivery is None:
        style = await resolve_employee_style(str(target.project_id), target.agent_id)
        target = target.model_copy(update={
            "humanization_skill_name": style.skill_name,
            "humanization_skill_enabled": style.enabled,
        })
    row, claimed = _claim(db, target, request)
    if claimed and target.requires_external_delivery:
        try:
            response = await forward_staff_platform_message(
                target, request.payload, request.client_msg_no
            )
            if 200 <= response.status_code < 300 and response.json().get("ok") is True:
                row.external_status = "sent"
            elif response.status_code in DEFINITE_REJECTIONS:
                row.external_status = "failed"
                row.error_code, row.error_message = _rejection(response, target)
            else:
                row.external_status = "unknown"
                row.error_code = f"PLATFORM_HTTP_{response.status_code}"
                row.error_message = UNKNOWN_MESSAGE
        except Exception:
            row.external_status = "unknown"
            row.error_code = "PLATFORM_RECEIPT_UNKNOWN"
            row.error_message = UNKNOWN_MESSAGE
        row.updated_at = utc_now()
        # If this commit fails, the pre-send 'sending' intent remains. It must
        # never be automatically retried against the external channel.
        db.commit()
    await sync_delivery_history(db, row.id)
    db.refresh(row)
    return receipt(row)
