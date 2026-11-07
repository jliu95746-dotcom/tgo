"""Cloud ownership and original-device delivery are independent identities."""

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import check_user_permission
from app.models import (
    ChannelMember,
    Staff,
    Visitor,
    VisitorAssignmentHistory,
    VisitorSession,
)
from app.models.staff_message_delivery import StaffMessageDelivery, utc_now
from app.models.wechat_staff_delivery import WeChatStaffDelivery
from app.schemas.staff_delivery import StaffDeliveryRequest
from app.schemas.wechat_staff_delivery import WeChatDeliveryReply
from app.schemas.wukongim import WuKongIMChannelMessageSyncResponse
from app.services.company_entitlements import require_human_service
from app.services.staff_delivery_training import (
    authorize_training,
    training_snapshot,
)
from app.services.staff_message_target import StaffMessageTarget

CLAIM_SECONDS = 120


def delivery_record(db: Session, delivery_id: UUID) -> StaffMessageDelivery:
    delivery = db.get(StaffMessageDelivery, delivery_id)
    if delivery is None:
        raise HTTPException(409, "人工发送记录已不可用")
    return delivery


def mask_bridge_history(
    result: WuKongIMChannelMessageSyncResponse,
) -> WuKongIMChannelMessageSyncResponse:
    (
        'Keep the sync cursor while old bridges skip already delivered human '
        'text.'
    )
    messages = []
    for message in result.messages:
        extra = message.payload.get("extra")
        if isinstance(extra, dict) and extra.get("wechat_local_delivery_id"):
            message = message.model_copy(
                update={
                    "payload": {
                        "type": 1000,
                        "content": "",
                        "extra": {
                            "wechat_local_delivery_id": extra[
                                "wechat_local_delivery_id"
                            ],
                            "wechat_local_delivered": True,
                        },
                    }
                }
            )
        messages.append(message)
    return result.model_copy(update={"messages": messages})


@dataclass(frozen=True)
class Ownership:
    visitor: Visitor
    session_id: UUID
    staff_id: UUID
    revision: UUID | None


def lock_context(
    db: Session, project: UUID, visitor_id: UUID, staff_id: UUID
) -> Ownership:
    # Transfer/close operations also lock the visitor first.

    visitor = db.scalar(
        select(Visitor)
        .where(
            Visitor.id == visitor_id,
            Visitor.project_id == project,
            Visitor.deleted_at.is_(None),
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if visitor is None:
        raise HTTPException(409, "客户已不可接待")
    owner = db.scalar(
        select(VisitorSession)
        .where(
            VisitorSession.visitor_id == visitor_id,
            VisitorSession.project_id == project,
        )
        .order_by(VisitorSession.created_at.desc(), VisitorSession.id.desc())
        .limit(1)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    staff = db.scalar(
        select(Staff)
        .where(Staff.id == staff_id, Staff.project_id == project)
        .execution_options(populate_existing=True)
        .with_for_update(read=True)
    )
    if (
        owner is None
        or owner.staff_id != staff_id
        or owner.status != "open"
        or staff is None
        or not staff.account_enabled
        or not staff.is_active
        or staff.deleted_at is not None
        or staff.role == "agent"
        or not check_user_permission(db, staff, "chat:send")
    ):
        raise HTTPException(409, "接待归属或员工发送权限已变化")
    membership = db.scalar(
        select(ChannelMember.id).where(
            ChannelMember.project_id == project,
            ChannelMember.channel_id == f"{visitor_id}-vtr",
            ChannelMember.channel_type == 251,
            ChannelMember.member_id == staff_id,
            ChannelMember.member_type == "staff",
            ChannelMember.deleted_at.is_(None),
        )
    )
    if membership is None:
        raise HTTPException(409, "员工已不在当前接待会话")
    revision = db.scalar(
        select(VisitorAssignmentHistory.id)
        .where(
            VisitorAssignmentHistory.visitor_id == visitor_id,
            VisitorAssignmentHistory.project_id == project,
        )
        .order_by(
            VisitorAssignmentHistory.created_at.desc(),
            VisitorAssignmentHistory.id.desc(),
        )
        .limit(1)
    )
    require_human_service(db, project, visitor_id)
    return Ownership(visitor, owner.id, staff_id, revision)


def enqueue(
    db: Session, target: StaffMessageTarget, request: StaffDeliveryRequest
) -> StaffMessageDelivery:
    text = request.payload.get("content")
    if (
        request.payload.get("type") != 1
        or not isinstance(text, str)
        or not 0 < len(text.strip()) <= 8000
    ):
        raise HTTPException(400, "个人微信人工回复须为 1 到 8000 字文字")
    owner = lock_context(
        db, target.project_id, target.visitor_id, target.staff_id
    )
    if (owner.visitor.platform_id, owner.visitor.platform_open_id) != (
        target.platform_id,
        target.platform_open_id,
    ):
        raise HTTPException(409, "原微信接入渠道已变化")
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "platform_id": str(target.platform_id),
                "recipient": target.platform_open_id,
                "request": request.model_dump(
                    mode="json", exclude={"retry_failed"}
                ),
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    existing = db.scalar(
        select(StaffMessageDelivery)
        .where(
            StaffMessageDelivery.project_id == target.project_id,
            StaffMessageDelivery.staff_id == target.staff_id,
            StaffMessageDelivery.client_msg_no == request.client_msg_no,
        )
        .with_for_update()
    )
    if existing:
        if existing.request_fingerprint != fingerprint:
            raise HTTPException(409, "相同消息编号对应的内容或收件人不一致")
        # Even definitive cancellation needs a newly reviewed human message.

        return existing
    snapshot = training_snapshot(request)
    authorize_training(target, snapshot)
    delivery_id = uuid4()
    payload = dict(request.payload)
    extra = payload.get("extra")
    payload["extra"] = {
        **(extra if isinstance(extra, dict) else {}),
        "wechat_local_delivery_id": str(delivery_id),
    }
    row = StaffMessageDelivery(
        id=delivery_id,
        project_id=target.project_id,
        staff_id=target.staff_id,
        visitor_id=target.visitor_id,
        channel_id=target.channel_id,
        channel_type=target.channel_type,
        client_msg_no=request.client_msg_no,
        request_fingerprint=fingerprint,
        payload=payload,
        external_status="queued",
        history_status="pending",
        training_snapshot=snapshot,
        training_status="pending" if snapshot else "none",
    )
    db.add(row)
    try:
        # The child queue references this row; flush before adding the child.

        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "消息编号已被其他发送占用，请刷新状态后核对") from None
    db.add(
        WeChatStaffDelivery(
            id=delivery_id,
            project_id=target.project_id,
            platform_id=target.platform_id,
            visitor_id=target.visitor_id,
            platform_open_id=target.platform_open_id,
            owner_session_id=owner.session_id,
            owner_staff_id=owner.staff_id,
            owner_revision=owner.revision,
            text=" ".join(text.strip().splitlines()),
            state="queued",
        )
    )
    # Keep assist mode; an ordinary manual send takes ownership from auto AI.

    if owner.visitor.service_mode != "assist":
        owner.visitor.service_mode = "manual"
    owner.visitor.ai_disabled = True
    db.commit()
    return row


def scoped_row(
    db: Session, delivery_id: UUID, platform_id: UUID, visitor: Visitor
) -> WeChatStaffDelivery:
    row = db.get(WeChatStaffDelivery, delivery_id)
    if row is None:
        raise HTTPException(404, "未找到个人微信发送任务")
    if (
        row.project_id,
        row.platform_id,
        row.visitor_id,
        row.platform_open_id,
    ) != (
        visitor.project_id,
        platform_id,
        visitor.id,
        visitor.platform_open_id,
    ):
        raise HTTPException(403, "发送任务不属于当前微信会话")
    # Serializes claims with transfer/close/new submissions before locking
    # queue.

    if (
        db.scalar(
            select(Visitor.id)
            .where(Visitor.id == visitor.id)
            .with_for_update()
        )
        is None
    ):
        raise HTTPException(409, "原微信会话已不可用")
    db.scalar(
        select(VisitorSession.id)
        .where(
            VisitorSession.visitor_id == visitor.id,
            VisitorSession.project_id == visitor.project_id,
        )
        .order_by(VisitorSession.created_at.desc(), VisitorSession.id.desc())
        .limit(1)
        .with_for_update()
    )
    return db.scalars(
        select(WeChatStaffDelivery)
        .where(WeChatStaffDelivery.id == delivery_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    ).one()


def expire(db: Session, row: WeChatStaffDelivery) -> None:
    claimed = row.claimed_at
    if row.state == "sending" and claimed is not None:
        if claimed.tzinfo is None:
            claimed = claimed.replace(tzinfo=timezone.utc)
        if utc_now() - claimed > timedelta(seconds=CLAIM_SECONDS):
            row.state = "unknown"
            delivery = delivery_record(db, row.id)
            delivery.external_status = "unknown"
            delivery.error_code = "WECHAT_RECEIPT_UNKNOWN"
            db.commit()


def checked_owner(db: Session, row: WeChatStaffDelivery) -> None:
    try:
        current = lock_context(
            db, row.project_id, row.visitor_id, row.owner_staff_id
        )
        if (current.session_id, current.staff_id, current.revision) != (
            row.owner_session_id,
            row.owner_staff_id,
            row.owner_revision,
        ):
            raise HTTPException(409, "会话已转接或接待授权已变化")
        if (current.visitor.platform_id, current.visitor.platform_open_id) != (
            row.platform_id,
            row.platform_open_id,
        ):
            raise HTTPException(409, "原微信接入渠道已变化")
    except HTTPException:
        # A claim may already have caused an external send; never mark it
        # failed.

        if row.state == "queued":
            row.state = "cancelled"
            delivery = delivery_record(db, row.id)
            delivery.external_status = "failed"
            delivery.error_code = "WECHAT_OWNER_CHANGED"
            delivery.error_message = "会话已转接或权限变化，这条回复已停止，请由当前接待人重新确认。"
            db.commit()
        raise HTTPException(409, "会话已转接或接待权限已变化，停止发送") from None


def check(
    db: Session,
    delivery_id: UUID,
    platform_id: UUID,
    visitor: Visitor,
    attempt_id: UUID | None = None,
) -> WeChatStaffDelivery:
    row = scoped_row(db, delivery_id, platform_id, visitor)
    expire(db, row)
    if row.state not in {"queued", "sending"}:
        raise HTTPException(409, "该回复不可再次发送，请核对发送结果")
    if row.state == "sending" and row.attempt_id != attempt_id:
        raise HTTPException(409, "其他桥接进程已取得发送任务")
    checked_owner(db, row)
    first = db.scalar(
        select(WeChatStaffDelivery.id)
        .where(
            WeChatStaffDelivery.platform_id == platform_id,
            WeChatStaffDelivery.visitor_id == visitor.id,
            WeChatStaffDelivery.state.in_(["queued", "sending", "unknown"]),
        )
        .order_by(WeChatStaffDelivery.created_at, WeChatStaffDelivery.id)
        .limit(1)
    )
    if first != row.id:
        raise HTTPException(409, "前一条回复仍在发送或等待核对")
    return row


def ready(
    db: Session, platform_id: UUID, visitor: Visitor
) -> list[WeChatDeliveryReply]:
    rows = list(
        db.scalars(
            select(WeChatStaffDelivery)
            .where(
                WeChatStaffDelivery.platform_id == platform_id,
                WeChatStaffDelivery.visitor_id == visitor.id,
                WeChatStaffDelivery.project_id == visitor.project_id,
                WeChatStaffDelivery.state.in_(
                    ["queued", "sending", "unknown"]
                ),
            )
            .order_by(WeChatStaffDelivery.created_at, WeChatStaffDelivery.id)
            .limit(50)
        )
    )
    for candidate in rows:
        row = scoped_row(db, candidate.id, platform_id, visitor)
        expire(db, row)
        if row.state == "unknown":
            return []
        try:
            checked_owner(db, row)
        except HTTPException:
            if row.state == "cancelled":
                continue
            return []
        if row.state not in {"queued", "sending"}:
            continue
        delivery = delivery_record(db, row.id)
        return [
            WeChatDeliveryReply(
                delivery_id=row.id,
                client_msg_no=delivery.client_msg_no,
                text=row.text,
                created_at=row.created_at,
            )
        ]
    return []


def claim(
    db: Session,
    delivery_id: UUID,
    platform_id: UUID,
    visitor: Visitor,
    attempt_id: UUID,
) -> UUID:
    row = check(db, delivery_id, platform_id, visitor, attempt_id)
    if row.state == "sending":
        assert row.claim_id is not None
        claim_id = row.claim_id
        db.commit()
        return claim_id
    row.state, row.attempt_id, row.claim_id, row.claimed_at = (
        "sending",
        attempt_id,
        uuid4(),
        utc_now(),
    )
    delivery = delivery_record(db, row.id)
    delivery.external_status = "sending"
    delivery.updated_at = row.claimed_at
    claim_id = row.claim_id
    db.commit()
    return claim_id


async def confirm(
    db: Session,
    delivery_id: UUID,
    platform_id: UUID,
    visitor: Visitor,
    claim_id: UUID,
    result: str,
    proof: str | None,
) -> None:
    row = scoped_row(db, delivery_id, platform_id, visitor)
    if row.claim_id != claim_id or row.state not in {
        "sending",
        "unknown",
        "sent",
    }:
        raise HTTPException(409, "发送回执与原任务不一致")
    if result == "sent" and not proof:
        raise HTTPException(422, "缺少微信实际发送回执")
    if row.state == "sent" and (result != "sent" or row.receipt != proof):
        raise HTTPException(409, "同一发送任务的回执不一致")
    delivery = delivery_record(db, row.id)
    if result == "sent":
        row.state, row.receipt = "sent", proof
        row.delivered_at = row.delivered_at or utc_now()
        delivery.external_status = "sent"
        delivery.error_code = delivery.error_message = None
    else:
        row.state, delivery.external_status = "unknown", "unknown"
        delivery.error_code = "WECHAT_RECEIPT_UNKNOWN"
    db.commit()
    if result == "sent":
        from app.services.staff_delivery import sync_delivery_history

        await sync_delivery_history(db, row.id)
