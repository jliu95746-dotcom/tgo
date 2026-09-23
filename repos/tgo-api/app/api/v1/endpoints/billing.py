"""Company-scoped billing reads and administrator purchase commands."""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1.endpoints.company_membership import require_company_features
from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_active_user, require_admin
from app.models import Staff
from app.models.billing import BillingOrder, BillingPlan
from app.models.company_account import AICreditBatch, CompanyAccount
from app.schemas.billing import (
    OrderCreate,
    OrderResponse,
    PlanDefinition,
    PlanResponse,
    QuoteDetails,
    QuoteRequest,
    QuoteResponse,
    SubscriptionResponse,
)
from app.services.billing_orders import create_order
from app.services.billing_quotes import conflict, create_quote
from app.services.company_email import utc
from app.services.company_membership import seat_usage

router = APIRouter(dependencies=[Depends(require_company_features)])


def plan_response(plan: BillingPlan) -> PlanResponse:
    return PlanResponse(
        id=plan.id,
        code=plan.code,
        version=plan.version,
        status=plan.status,
        definition=PlanDefinition.model_validate(plan.definition),
    )


def require_purchases() -> None:
    if not settings.SAAS_NEW_PURCHASES_ENABLED:
        raise conflict("购买入口暂未开放", "PURCHASES_DISABLED")


@router.get("/plans", response_model=list[PlanResponse])
def plans(db: Session = Depends(get_db)) -> list[PlanResponse]:
    return [
        plan_response(p)
        for p in db.scalars(
            select(BillingPlan)
            .where(BillingPlan.status == "published")
            .order_by(BillingPlan.code, BillingPlan.version.desc())
        )
    ]


@router.get("/subscription", response_model=SubscriptionResponse)
def subscription(
    db: Session = Depends(get_db), actor: Staff = Depends(get_current_active_user)
) -> SubscriptionResponse:
    now = datetime.now(timezone.utc)
    account = db.get(CompanyAccount, actor.project_id)
    used, reserved = seat_usage(db, actor.project_id)
    current = (
        db.get(BillingPlan, account.plan_id) if account and account.plan_id else None
    )
    state = account.status if account else "legacy"
    if (
        account
        and state in {"active", "trial"}
        and account.expires_at
        and utc(account.expires_at) <= now
    ):
        state = "expired"
    remaining = (
        db.scalar(
            select(func.coalesce(func.sum(AICreditBatch.remaining), 0)).where(
                AICreditBatch.project_id == actor.project_id,
                AICreditBatch.expires_at > now,
            )
        )
        or 0
    )
    return SubscriptionResponse(
        status=state,
        expires_at=account.expires_at if account else None,
        plan=plan_response(current) if current else None,
        seats=account.seat_limit if account else None,
        seats_used=used,
        seats_reserved=reserved,
        ai_remaining=remaining,
        server_time=now,
        months=account.billing_months if account else None,
        version=account.version if account else None,
    )


@router.post(
    "/quotes", response_model=QuoteResponse, dependencies=[Depends(require_purchases)]
)
def quote(
    payload: QuoteRequest,
    db: Session = Depends(get_db),
    actor: Staff = Depends(require_admin()),
) -> QuoteResponse:
    result = create_quote(db, actor, payload)
    db.commit()
    return QuoteResponse(
        id=result.id,
        amount=result.amount,
        expires_at=result.expires_at,
        details=QuoteDetails.model_validate(result.details),
    )


@router.post(
    "/orders", response_model=OrderResponse, dependencies=[Depends(require_purchases)]
)
def order_create(
    payload: OrderCreate,
    db: Session = Depends(get_db),
    actor: Staff = Depends(require_admin()),
) -> BillingOrder:
    order = create_order(db, actor, payload.quote_id)
    db.commit()
    return order


@router.get("/orders", response_model=list[OrderResponse])
def orders(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    actor: Staff = Depends(require_admin()),
) -> list[BillingOrder]:
    return list(
        db.scalars(
            select(BillingOrder)
            .where(BillingOrder.project_id == actor.project_id)
            .order_by(BillingOrder.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    )


@router.get("/orders/{order_id}", response_model=OrderResponse)
def order_detail(
    order_id: UUID,
    db: Session = Depends(get_db),
    actor: Staff = Depends(require_admin()),
) -> BillingOrder:
    order = db.get(BillingOrder, order_id)
    if order is None or order.project_id != actor.project_id:
        raise conflict("订单不存在", "ORDER_NOT_FOUND")
    return order
