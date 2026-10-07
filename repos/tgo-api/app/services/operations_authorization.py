"""Audited operator grants preserve paid orders and serialize subscription changes."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.billing import BillingAudit, BillingOrder, BillingPlan
from app.models.company_account import AICreditBatch, CompanyAccount
from app.models.platform_operator import PlatformOperator
from app.schemas.billing import PlanDefinition
from app.schemas.operations_management import (
    AuthorizationChange,
    AuthorizationPreview,
    AuthorizationSnapshot,
)
from app.services.billing_quotes import conflict, published_plan
from app.services.company_email import utc
from app.services.company_membership import lock_company, seat_usage


def preview_authorization(
    db: Session,
    project_id: UUID,
    payload: AuthorizationChange,
) -> AuthorizationPreview:
    account = lock_company(db, project_id)
    version = account.version if account else 0
    if payload.expected_version != version:
        raise conflict("商家授权已被修改，请刷新后重新预览", "AUTHORIZATION_CONFLICT")
    now = datetime.now(timezone.utc)
    if payload.expires_at <= now:
        raise conflict("新的授权到期时间必须晚于当前时间")
    plan = published_plan(db, payload.plan_id)
    used, reserved = seat_usage(db, project_id)
    if payload.seats < used + reserved:
        raise conflict("坐席上限不能少于已启用账号和有效邀请之和", "SEAT_LIMIT")
    unpaid_fulfillment = db.scalar(
        select(BillingOrder.id)
        .where(
            BillingOrder.project_id == project_id,
            BillingOrder.payment_status == "paid",
            BillingOrder.fulfillment_status != "applied",
            BillingOrder.refunded_amount < BillingOrder.amount,
        )
        .limit(1)
    )
    if unpaid_fulfillment is not None:
        raise conflict("该商家有已付款但尚未完成授权的订单，请先处理订单异常")
    previous_plan = (
        db.get(BillingPlan, account.plan_id) if account and account.plan_id else None
    )
    before = AuthorizationSnapshot(
        plan_id=account.plan_id if account else None,
        plan_name=PlanDefinition.model_validate(previous_plan.definition).name
        if previous_plan
        else None,
        status=account.status if account else "legacy",
        expires_at=account.expires_at if account else None,
        seats=account.seat_limit if account else None,
        version=version,
    )
    after = AuthorizationSnapshot(
        plan_id=plan.id,
        plan_name=PlanDefinition.model_validate(plan.definition).name,
        status="suspended" if account and account.status == "suspended" else "active",
        expires_at=payload.expires_at,
        seats=payload.seats,
        version=version + 1,
        ai_credits=payload.ai_credits,
    )
    return AuthorizationPreview(
        before=before, after=after, used_seats=used, reserved_seats=reserved
    )


def change_authorization(
    db: Session,
    operator: PlatformOperator,
    project_id: UUID,
    payload: AuthorizationChange,
) -> None:
    account = lock_company(db, project_id)
    request = payload.model_dump(mode="json")
    prior = db.get(BillingAudit, payload.request_id)
    if prior is not None:
        if (
            prior.project_id != project_id
            or prior.action != "authorization.change"
            or prior.detail.get("request") != request
        ):
            raise conflict("操作编号已用于其他授权变更")
        return
    preview = preview_authorization(db, project_id, payload)
    now = datetime.now(timezone.utc)
    if account is None:
        account = CompanyAccount(project_id=project_id)
        db.add(account)
    account.plan_id = payload.plan_id
    account.seat_limit = payload.seats
    account.expires_at = payload.expires_at
    account.operator_override_until = payload.expires_at
    account.status = preview.after.status
    account.version = preview.after.version
    account.started_at = account.started_at or now
    account.billing_months = account.billing_months or 1
    account.anchor_day = account.anchor_day or now.day
    if payload.ai_credits:
        db.add(
            AICreditBatch(
                project_id=project_id,
                source_key=f"authorization:{payload.request_id}",
                kind="adjustment",
                amount=payload.ai_credits,
                remaining=payload.ai_credits,
                expires_at=payload.expires_at,
            )
        )
    db.add(
        BillingAudit(
            id=payload.request_id,
            operator_id=operator.id,
            project_id=project_id,
            action="authorization.change",
            reason=payload.reason,
            detail={
                "request": request,
                "before": preview.before.model_dump(mode="json"),
                "after": preview.after.model_dump(mode="json"),
            },
        )
    )
    db.flush()


def operator_authorization_active(account: CompanyAccount, now: datetime) -> bool:
    return (
        account.operator_override_until is not None
        and utc(account.operator_override_until) > now
    )
