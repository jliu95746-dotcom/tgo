"""Platform billing controls require the independent operator identity."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.endpoints.billing import plan_response
from app.api.v1.endpoints.operations import require_operator, require_commercial_operations
from app.core.database import get_db
from app.models.billing import BillingOrder, BillingPlan
from app.models.platform_operator import PlatformOperator
from app.schemas.billing import (
    STANDARD_PLAN_RANKS, OrderResponse, PlanCreate, PlanResponse,
    StandardPlanDrafts, StandardPlanPublication,
)
from app.services.billing_plans import (
    change_plan_state, create_plan, create_standard_drafts, publish_standard_drafts,
)
from app.services.billing_quotes import conflict

router = APIRouter(
    dependencies=[Depends(require_commercial_operations), Depends(require_operator)]
)


@router.get("/plans", response_model=list[PlanResponse])
def plans(
    limit: int = Query(100, ge=1, le=100),
    offset: int = Query(0, ge=0),
    standard_only: bool = False,
    target_project_id: UUID | None = None,
    db: Session = Depends(get_db),
) -> list[PlanResponse]:
    query = select(BillingPlan)
    target = BillingPlan.definition["target_project_id"].as_string()
    if target_project_id is not None:
        query = query.where(target == str(target_project_id))
    elif standard_only:
        query = query.where(target.is_(None), BillingPlan.code.in_(STANDARD_PLAN_RANKS))
    return [
        plan_response(p)
        for p in db.scalars(
            query
            .order_by(BillingPlan.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ]


@router.post("/plans", response_model=PlanResponse)
def new_plan(
    payload: PlanCreate,
    db: Session = Depends(get_db),
    operator: PlatformOperator = Depends(require_operator),
) -> PlanResponse:
    if payload.definition.target_project_id is None:
        raise conflict("请通过三档草稿统一保存公开套餐")
    try:
        plan = create_plan(db, operator, payload)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise conflict("套餐版本已被其他运营人员创建，请重试") from exc
    return plan_response(plan)


@router.post("/plans/standard-drafts", response_model=list[PlanResponse])
def standard_drafts(
    payload: StandardPlanDrafts,
    db: Session = Depends(get_db),
    operator: PlatformOperator = Depends(require_operator),
) -> list[PlanResponse]:
    try:
        drafts = create_standard_drafts(db, operator, payload.plans)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise conflict("套餐版本已被其他运营人员创建，请重试") from exc
    return [plan_response(draft) for draft in drafts]


@router.post("/plans/standard-drafts/publish", response_model=list[PlanResponse])
def standard_publication(
    payload: StandardPlanPublication,
    db: Session = Depends(get_db),
    operator: PlatformOperator = Depends(require_operator),
) -> list[PlanResponse]:
    published = publish_standard_drafts(db, operator, payload.plan_ids)
    db.commit()
    return [plan_response(plan) for plan in published]


@router.post("/plans/{plan_id}/{state}", response_model=PlanResponse)
def plan_state(
    plan_id: UUID,
    state: Literal["published", "retired"],
    db: Session = Depends(get_db),
    operator: PlatformOperator = Depends(require_operator),
) -> PlanResponse:
    existing = db.get(BillingPlan, plan_id)
    if existing is not None and existing.code in STANDARD_PLAN_RANKS:
        raise conflict("请通过三档发布统一调整公开套餐")
    plan = change_plan_state(db, operator, plan_id, state)
    db.commit()
    return plan_response(plan)


@router.get("/orders", response_model=list[OrderResponse])
def orders(
    project_id: UUID | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[BillingOrder]:
    query = select(BillingOrder)
    if project_id is not None:
        query = query.where(BillingOrder.project_id == project_id)
    return list(
        db.scalars(
            query.order_by(BillingOrder.created_at.desc()).offset(offset).limit(limit)
        )
    )
