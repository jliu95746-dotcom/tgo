"""Company ledger overview and compensating adjustments without erasing history."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Project
from app.models.billing import BillingAudit, BillingOrder, BillingPlan
from app.models.company_account import AICreditBatch, CompanyAccount
from app.models.platform_operator import PlatformOperator
from app.schemas.operations_companies import CreditAdjustment, OperationsCompany
from app.schemas.billing import PlanDefinition
from app.services.billing_quotes import conflict
from app.services.company_email import utc
from app.services.company_membership import lock_company, seat_usage


def companies(
    db: Session, offset: int, limit: int, project_id: UUID | None = None
) -> list[OperationsCompany]:
    now = datetime.now(timezone.utc)
    results: list[OperationsCompany] = []
    query = select(Project).where(Project.deleted_at.is_(None))
    if project_id is not None:
        query = query.where(Project.id == project_id)
    for project in db.scalars(
        query
        .order_by(Project.created_at.desc())
        .offset(offset)
        .limit(limit)
    ):
        account = db.get(CompanyAccount, project.id)
        plan = (
            db.get(BillingPlan, account.plan_id)
            if account and account.plan_id
            else None
        )
        used, reserved = seat_usage(db, project.id)
        state = account.status if account else "legacy"
        if (
            account
            and account.expires_at
            and state in {"trial", "active"}
            and utc(account.expires_at) <= now
        ):
            state = "expired"
        remaining = (
            db.scalar(
                select(func.coalesce(func.sum(AICreditBatch.remaining), 0)).where(
                    AICreditBatch.project_id == project.id,
                    AICreditBatch.expires_at > now,
                )
            )
            or 0
        )
        exceptions = (
            db.scalar(
                select(func.count())
                .select_from(BillingOrder)
                .where(
                    BillingOrder.project_id == project.id,
                    BillingOrder.payment_status == "paid",
                    BillingOrder.fulfillment_status != "applied",
                    BillingOrder.refunded_amount < BillingOrder.amount,
                )
            )
            or 0
        )
        results.append(
            OperationsCompany(
                id=project.id,
                name=project.name,
                status=state,
                plan_name=PlanDefinition.model_validate(plan.definition).name
                if plan
                else None,
                expires_at=account.expires_at if account else None,
                seats=account.seat_limit if account else None,
                used=used,
                reserved=reserved,
                ai_remaining=remaining,
                order_exceptions=exceptions,
            )
        )
    return results


def adjust_credit(
    db: Session, operator: PlatformOperator, project_id: UUID, payload: CreditAdjustment
) -> None:
    if lock_company(db, project_id) is None:
        raise conflict("现有企业尚未迁入订阅管理，不能调整次数")
    request = payload.model_dump(mode="json")
    existing = db.get(BillingAudit, payload.request_id)
    if existing is not None:
        if (
            existing.project_id != project_id
            or existing.action != "quota.adjust"
            or existing.detail.get("request") != request
        ):
            raise conflict("操作编号已用于其他调整，请重新核对")
        return
    now = datetime.now(timezone.utc)
    changes: list[dict[str, str | int]] = []
    if payload.delta > 0:
        assert payload.expires_at is not None
        if not now < payload.expires_at <= now + timedelta(days=366):
            raise conflict("调整次数的有效期应在当前时间之后且不超过一年")
        batch = AICreditBatch(
            project_id=project_id,
            source_key=f"adjust:{payload.request_id}",
            kind="adjustment",
            amount=payload.delta,
            remaining=payload.delta,
            expires_at=payload.expires_at,
        )
        db.add(batch)
        db.flush()
        changes.append({"batch_id": str(batch.id), "delta": payload.delta})
    else:
        batches = list(
            db.scalars(
                select(AICreditBatch)
                .where(
                    AICreditBatch.project_id == project_id,
                    AICreditBatch.expires_at > now,
                    AICreditBatch.remaining > 0,
                )
                .order_by(AICreditBatch.expires_at, AICreditBatch.id)
                .with_for_update()
            )
        )
        remaining = -payload.delta
        if sum(batch.remaining for batch in batches) < remaining:
            raise conflict("可用次数不足，不能扣除已使用或正在预占的次数")
        for batch in batches:
            amount = min(remaining, batch.remaining)
            if amount:
                batch.remaining -= amount
                remaining -= amount
                changes.append({"batch_id": str(batch.id), "delta": -amount})
            if not remaining:
                break
    db.add(
        BillingAudit(
            id=payload.request_id,
            operator_id=operator.id,
            project_id=project_id,
            action="quota.adjust",
            reason=payload.reason,
            detail={"request": request, "changes": changes},
        )
    )
    db.flush()
