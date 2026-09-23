"""Published plans are immutable; changes create a new audited version."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.billing import BillingAudit, BillingPlan
from app.models.platform_operator import PlatformOperator
from app.schemas.billing import PlanCreate, PlanDefinition
from app.services.billing_quotes import conflict


def create_plan(
    db: Session, operator: PlatformOperator, payload: PlanCreate
) -> BillingPlan:
    version = (
        db.scalar(
            select(func.max(BillingPlan.version)).where(
                BillingPlan.code == payload.code
            )
        )
        or 0
    ) + 1
    plan = BillingPlan(
        code=payload.code,
        version=version,
        status="draft",
        definition=payload.definition.model_dump(mode="json"),
    )
    db.add(plan)
    db.flush()
    db.add(
        BillingAudit(
            operator_id=operator.id,
            action="plan.create",
            reason="创建套餐草稿",
            detail={"plan_id": str(plan.id), "version": version},
        )
    )
    return plan


def change_plan_state(
    db: Session, operator: PlatformOperator, plan_id: UUID, state: str
) -> BillingPlan:
    plan = db.get(BillingPlan, plan_id)
    if plan is None:
        raise conflict("套餐不存在")
    versions = list(
        db.scalars(
            select(BillingPlan)
            .where(BillingPlan.code == plan.code)
            .order_by(BillingPlan.version)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    if state not in {"published", "retired"}:
        raise conflict("套餐状态不支持")
    if state == "published":
        if plan.status != "draft":
            raise conflict("只有草稿可以发布；调整价格请创建新版本")
        PlanDefinition.model_validate(plan.definition)
        # Lock all versions in deterministic order before replacing the visible version.
        for old in versions:
            if old.id != plan.id and old.status == "published":
                old.status = "retired"
    plan.status = state
    db.add(
        BillingAudit(
            operator_id=operator.id,
            action=f"plan.{state}",
            reason="运营调整套餐状态",
            detail={"plan_id": str(plan.id)},
        )
    )
    db.flush()
    return plan
