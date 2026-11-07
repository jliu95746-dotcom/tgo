"""Published plans are immutable; changes create a new audited version."""

from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models import Project
from app.models.billing import BillingAudit, BillingPlan
from app.models.platform_operator import PlatformOperator
from app.schemas.billing import (
    CUSTOM_PLAN_RANK,
    STANDARD_PLAN_NAMES,
    STANDARD_PLAN_RANKS,
    PlanCreate,
    PlanDefinition,
)
from app.services.billing_quotes import conflict


def standard_annual_price(monthly_cents: int) -> int:
    yearly_yuan = monthly_cents * 96 // 1000
    return (yearly_yuan - (yearly_yuan - 8) % 10) * 100


def catalogue_plans(
    db: Session, project_id: UUID | None = None
) -> list[BillingPlan]:
    target = BillingPlan.definition["target_project_id"].as_string()
    public = and_(target.is_(None), BillingPlan.code.in_(STANDARD_PLAN_RANKS))
    visible = (
        public
        if project_id is None
        else or_(public, target == str(project_id))
    )
    return list(
        db.scalars(
            select(BillingPlan)
            .where(BillingPlan.status == "published", visible)
            .order_by(
                BillingPlan.definition["rank"].as_integer(), BillingPlan.code
            )
        )
    )


def _validate_audience(
    db: Session, code: str, definition: PlanDefinition
) -> None:
    target = definition.target_project_id
    if target is None:
        if STANDARD_PLAN_RANKS.get(code) != definition.rank:
            raise conflict("公开目录只支持三个标准套餐档位")
        return
    if code != f"custom-{target.hex}" or definition.rank != CUSTOM_PLAN_RANK:
        raise conflict("企业定制套餐的编号和级别必须与指定企业匹配")
    project = db.get(Project, target)
    if project is None or project.deleted_at is not None:
        raise conflict("指定企业不存在或已删除")


def create_plan(
    db: Session, operator: PlatformOperator, payload: PlanCreate
) -> BillingPlan:
    _validate_audience(db, payload.code, payload.definition)
    if (
        payload.definition.target_project_id is None
        and payload.definition.annual_price
        != standard_annual_price(payload.definition.monthly_price)
    ):
        raise conflict("标准套餐年价须按月价八折向下取尾数为 8 的整数")
    definition = payload.definition
    if definition.target_project_id is None:
        definition = definition.model_copy(
            update={"name": STANDARD_PLAN_NAMES[payload.code]}
        )
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
        definition=definition.model_dump(mode="json"),
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


def _validate_standard_terms(definitions: list[PlanDefinition]) -> None:
    if any(definition.channel_limit is not None for definition in definitions):
        raise conflict("三档标准套餐均开放全部渠道，请重新保存草稿")
    add_on_prices = {
        (
            definition.seat_monthly_price,
            definition.seat_annual_price,
            definition.ai_pack_price,
            definition.ai_pack_replies,
        )
        for definition in definitions
    }
    if len(add_on_prices) != 1:
        raise conflict("三个公开套餐必须使用同一套加购规则")
    ordered = sorted(definitions, key=lambda item: item.rank)
    for smaller, larger in zip(ordered, ordered[1:]):
        if (
            larger.monthly_price <= smaller.monthly_price
            or larger.seats < smaller.seats
            or larger.monthly_ai < smaller.monthly_ai
            or larger.knowledge_bytes < smaller.knowledge_bytes
        ):
            raise conflict("标准套餐的价格须逐档增加，坐席和额度须逐档持平或增加")


def create_standard_drafts(
    db: Session, operator: PlatformOperator, drafts: list[PlanCreate]
) -> list[BillingPlan]:
    if {draft.code for draft in drafts} != set(STANDARD_PLAN_RANKS) or any(
        draft.definition.target_project_id is not None for draft in drafts
    ):
        raise conflict("请同时配置三个公开套餐")
    _validate_standard_terms([draft.definition for draft in drafts])
    return [
        create_plan(db, operator, draft)
        for draft in sorted(drafts, key=lambda item: item.code)
    ]


def publish_standard_drafts(
    db: Session, operator: PlatformOperator, ids: list[UUID]
) -> list[BillingPlan]:
    drafts = list(
        db.scalars(
            select(BillingPlan)
            .where(BillingPlan.id.in_(ids))
            .order_by(BillingPlan.code)
        )
    )
    if {draft.code for draft in drafts} != set(STANDARD_PLAN_RANKS):
        raise conflict("请同时发布三个公开套餐的草稿")
    definitions = [
        PlanDefinition.model_validate(draft.definition) for draft in drafts
    ]
    for definition in definitions:
        if definition.target_project_id is not None:
            raise conflict("请同时发布三个公开套餐的草稿")
        if definition.annual_price != standard_annual_price(
            definition.monthly_price
        ):
            raise conflict("请重新保存三档草稿以应用当前年价规则")
    _validate_standard_terms(definitions)
    return [
        change_plan_state(db, operator, draft.id, "published")
        for draft in drafts
    ]


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
        _validate_audience(
            db, plan.code, PlanDefinition.model_validate(plan.definition)
        )
        # Lock all versions in deterministic order before replacing the visible
        # version.

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
