"""One quota reservation per reply round; all mutations share the company lock."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from pydantic import JsonValue
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.core.config import settings
from app.models.ai_usage import AIUsageMovement, AIUsageReservation
from app.models.billing import SubscriptionPeriod
from app.models.company_account import AICreditBatch, CompanyAccount
from app.services.billing_fulfillment import grant_due_month
from app.services.company_email import utc
from app.services.company_membership import lock_company


def require_service(account: CompanyAccount | None, now: datetime) -> None:
    if account is None:
        return
    if (
        account.status not in {"active", "trial"}
        or account.expires_at is None
        or utc(account.expires_at) <= now
    ):
        raise TGOAPIException(
            "企业套餐已到期或暂停，请由管理员续费", code="SUBSCRIPTION_EXPIRED", status_code=402
        )


def movement(
    db: Session, reservation: AIUsageReservation, kind: str, amount: int
) -> None:
    db.add(
        AIUsageMovement(
            project_id=reservation.project_id,
            reservation_id=reservation.id,
            batch_id=reservation.batch_id,
            business_key=f"{reservation.id}:{reservation.attempt}:{kind}",
            kind=kind,
            amount=amount,
        )
    )


def reserve(
    db: Session, project_id: UUID, round_key: str, now: datetime | None = None
) -> AIUsageReservation | None:
    now = now or datetime.now(timezone.utc)
    if not round_key or len(round_key) > 160:
        raise ValueError("Invalid AI reply round identifier")
    account = lock_company(db, project_id)
    if account is None:
        return None  # Legacy companies are not silently enrolled in billing.
    require_service(account, now)
    existing = db.scalar(
        select(AIUsageReservation)
        .where(
            AIUsageReservation.project_id == project_id,
            AIUsageReservation.round_key == round_key,
        )
        .execution_options(populate_existing=True)
    )
    if existing is not None and existing.status != "released":
        raise TGOAPIException(
            "该回复正在处理或已完成", code="AI_ROUND_ALREADY_CLAIMED", status_code=409
        )
    running = (
        db.scalar(
            select(func.count())
            .select_from(AIUsageReservation)
            .where(
                AIUsageReservation.project_id == project_id,
                AIUsageReservation.status.in_(["reserved", "publishing"]),
                AIUsageReservation.expires_at > now,
            )
        )
        or 0
    )
    if running >= settings.SAAS_AI_MAX_CONCURRENT_REPLIES:
        raise TGOAPIException(
            "同时处理的 AI 回复已达上限，请稍后重试", code="AI_CONCURRENCY_EXCEEDED", status_code=429
        )
    if account.status == "active":
        period = db.scalar(
            select(SubscriptionPeriod).where(
                SubscriptionPeriod.project_id == project_id,
                SubscriptionPeriod.starts_at <= now,
                SubscriptionPeriod.ends_at > now,
            )
        )
        if period is not None:
            grant_due_month(db, period, now)
    batch = db.scalar(
        select(AICreditBatch)
        .where(
            AICreditBatch.project_id == project_id,
            AICreditBatch.remaining > 0,
            AICreditBatch.expires_at > now,
        )
        .order_by(
            AICreditBatch.expires_at,
            case((AICreditBatch.kind == "pack", 1), else_=0),
            AICreditBatch.id,
        )
        .with_for_update()
        .limit(1)
    )
    if batch is None:
        raise TGOAPIException(
            "AI 回复次数已用尽，可继续人工接待或由管理员加购", code="AI_QUOTA_EXHAUSTED", status_code=402
        )
    batch.remaining -= 1
    if existing is None:
        existing = AIUsageReservation(
            project_id=project_id,
            round_key=round_key,
            batch_id=batch.id,
            expires_at=now + timedelta(minutes=15),
        )
        db.add(existing)
        db.flush()
    else:
        existing.status = "reserved"
        existing.batch_id = batch.id
        existing.lease_id = uuid4()
        existing.attempt += 1
        existing.expires_at = now + timedelta(minutes=15)
        existing.receipt = None
    movement(db, existing, "reserve", -1)
    db.flush()
    return existing


def owned(
    db: Session, project_id: UUID, reservation_id: UUID, lease_id: UUID
) -> AIUsageReservation:
    lock_company(db, project_id)
    row = db.scalar(
        select(AIUsageReservation)
        .where(
            AIUsageReservation.id == reservation_id,
            AIUsageReservation.project_id == project_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None or row.lease_id != lease_id:
        raise TGOAPIException(
            "AI 任务授权已失效", code="AI_RESERVATION_STALE", status_code=409
        )
    return row


def begin_publication(
    db: Session,
    project_id: UUID,
    reservation_id: UUID,
    lease_id: UUID,
    receipt: dict[str, JsonValue],
) -> None:
    row = owned(db, project_id, reservation_id, lease_id)
    require_service(db.get(CompanyAccount, project_id), datetime.now(timezone.utc))
    if row.status != "reserved" or utc(row.expires_at) <= datetime.now(timezone.utc):
        raise TGOAPIException(
            "AI 任务授权已失效", code="AI_RESERVATION_STALE", status_code=409
        )
    row.status = "publishing"
    row.receipt = receipt
    row.expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    db.flush()


def settle(db: Session, project_id: UUID, reservation_id: UUID, lease_id: UUID) -> None:
    row = owned(db, project_id, reservation_id, lease_id)
    if row.status == "settled":
        return
    if row.status not in {"publishing", "review"}:
        raise TGOAPIException(
            "尚未确认消息发送", code="AI_DELIVERY_UNCONFIRMED", status_code=409
        )
    row.status = "settled"
    row.settled_at = datetime.now(timezone.utc)
    movement(db, row, "settle", 0)
    db.flush()


def release(
    db: Session,
    project_id: UUID,
    reservation_id: UUID,
    lease_id: UUID,
    *,
    delivery_rejected: bool = False,
) -> None:
    row = owned(db, project_id, reservation_id, lease_id)
    if row.status in {"released", "settled"}:
        return
    if row.status != "reserved" and not delivery_rejected:
        row.status = (
            "review"  # Ambiguous sends need channel lookup, never blind refund.
        )
        return
    batch = db.get(AICreditBatch, row.batch_id)
    assert batch is not None
    batch.remaining += 1
    row.status = "released"
    movement(db, row, "release", 1)
    db.flush()
