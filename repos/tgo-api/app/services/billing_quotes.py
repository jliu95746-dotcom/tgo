"""Server-only commercial pricing; caller commits while holding company lock."""

from datetime import datetime, timedelta, timezone
from uuid import UUID
from typing import Literal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.models import Staff
from app.models.billing import (
    BillingOrder, BillingPlan, BillingQuote, SeatAddon, SubscriptionPeriod,
)
from app.models.company_account import AICreditBatch
from app.schemas.billing import (
    STANDARD_PLAN_RANKS,
    PeriodSnapshot,
    PlanDefinition,
    QuoteDetails,
    QuoteRequest,
)
from app.services.billing_calendar import (
    add_months,
    prorated_charge,
    remaining_fraction,
)
from app.services.company_email import utc
from app.services.company_membership import lock_company, seat_usage


def conflict(message: str, code: str = "BILLING_CONFLICT") -> TGOAPIException:
    return TGOAPIException(message, code=code, status_code=409)


def published_plan(
    db: Session, plan_id: UUID | None, project_id: UUID, *, legacy_code: str | None = None
) -> BillingPlan:
    plan = db.get(BillingPlan, plan_id) if plan_id else None
    if plan is None or plan.status != "published":
        raise conflict("套餐不存在或尚未发布", "PLAN_UNAVAILABLE")
    target = PlanDefinition.model_validate(plan.definition).target_project_id
    if target is not None and target != project_id:
        raise conflict("套餐不存在或尚未发布", "PLAN_UNAVAILABLE")
    if target is None and plan.code not in STANDARD_PLAN_RANKS and plan.code != legacy_code:
        raise conflict("套餐不存在或尚未发布", "PLAN_UNAVAILABLE")
    return plan


def cycle_months(value: int) -> Literal[1, 12]:
    if value == 1:
        return 1
    if value == 12:
        return 12
    raise conflict("订阅周期无效，请联系平台核对")


def next_add_on_tier(
    db: Session,
    plan_code: str,
    definition: PlanDefinition,
    dimension: Literal["seats", "monthly_ai"],
) -> PlanDefinition | None:
    rank = STANDARD_PLAN_RANKS.get(plan_code)
    if rank is None or definition.target_project_id is not None:
        return None
    higher_codes = [
        code for code, higher_rank in STANDARD_PLAN_RANKS.items()
        if higher_rank > rank
    ]
    if not higher_codes:
        return None
    plans = db.scalars(
        select(BillingPlan).where(
            BillingPlan.status == "published", BillingPlan.code.in_(higher_codes),
        )
    )
    tiers = sorted(plans, key=lambda plan: STANDARD_PLAN_RANKS[plan.code])
    for tier in tiers:
        candidate = PlanDefinition.model_validate(tier.definition)
        if (candidate.target_project_id is None
                and getattr(candidate, dimension) > getattr(definition, dimension)):
            return candidate
    return None


def ai_pack_window(
    periods: list[PeriodSnapshot], anchor: int, now: datetime, subscription_end: datetime,
) -> tuple[datetime, datetime]:
    period = next((p for p in periods if p.start <= now < p.end), None)
    if period is None:
        # Operator-authorized accounts can buy packs without a paid period.
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, min(add_months(start, 1), subscription_end)
    month_starts = [
        add_months(period.start, i, anchor_day=anchor) for i in range(period.months)
    ]
    month_start = max(start for start in month_starts if start <= now)
    month_end = min((start for start in month_starts if start > now), default=period.end)
    return month_start, month_end


def check_ai_add_on(
    db: Session, project_id: UUID, current: PlanDefinition, higher: PlanDefinition,
    month_start: datetime, additional_ai: int, now: datetime,
) -> None:
    purchased_at = func.coalesce(BillingOrder.paid_at, AICreditBatch.created_at)
    # Count spent purchases this month and honor balances sold under older terms.
    counted_credit = case(
        (purchased_at >= month_start, AICreditBatch.amount),
        else_=AICreditBatch.remaining,
    )
    packed_ai = db.scalar(
        select(func.coalesce(func.sum(counted_credit), 0))
        .select_from(AICreditBatch)
        .outerjoin(BillingOrder, BillingOrder.id == AICreditBatch.order_id)
        .where(
            AICreditBatch.project_id == project_id,
            AICreditBatch.kind == "pack",
            AICreditBatch.expires_at > now,
        )
    ) or 0
    if current.monthly_ai + packed_ai + additional_ai >= higher.monthly_ai:
        raise conflict(
            f"加购后 AI 回复额度将达到「{higher.name}」，请升级套餐",
            "ADD_ON_UPGRADE_REQUIRED",
        )


def create_quote(
    db: Session,
    staff: Staff,
    request: QuoteRequest,
    now: datetime | None = None,
) -> BillingQuote:
    now = now or datetime.now(timezone.utc)
    account = lock_company(db, staff.project_id, staff)
    if account is None or account.status in {"pending", "suspended"}:
        raise conflict("企业尚未激活或已停用，请联系平台")
    operator_override = (
        account.operator_override_until is not None
        and utc(account.operator_override_until) > now
    )
    if operator_override and request.kind in {"upgrade", "seats"}:
        raise conflict("运营授权期间，请联系运营调整套餐或坐席")
    current = db.get(BillingPlan, account.plan_id) if account.plan_id else None
    active = (
        account.status == "active"
        and account.expires_at is not None
        and utc(account.expires_at) > now
    )
    if request.kind in {"seats", "ai_pack"} and current is not None:
        if request.plan_id is not None and request.plan_id != current.id:
            raise conflict("加购必须使用当前套餐")
        plan = current  # Existing subscriptions retain their sold add-on prices.
    else:
        selected = request.plan_id
        if selected is None and current is not None and request.kind == "renew":
            selected = db.scalar(
                select(BillingPlan.id)
                .where(
                    BillingPlan.code == current.code, BillingPlan.status == "published"
                )
                .order_by(BillingPlan.version.desc())
                .limit(1)
            )
        legacy_code = current.code if current is not None and request.kind == "renew" else None
        plan = published_plan(db, selected or account.plan_id, staff.project_id, legacy_code=legacy_code)
    definition = PlanDefinition.model_validate(plan.definition)
    if definition.target_project_id not in (None, staff.project_id):
        raise conflict("套餐不存在或尚未发布", "PLAN_UNAVAILABLE")
    months = request.months
    extras = (
        db.scalar(
            select(func.coalesce(func.sum(SeatAddon.quantity), 0)).where(
                SeatAddon.project_id == staff.project_id,
                SeatAddon.starts_at <= now,
                SeatAddon.ends_at > now,
            )
        )
        or 0
    )
    periods = [
        PeriodSnapshot(
            id=p.id,
            start=utc(p.starts_at),
            end=utc(p.ends_at),
            months=cycle_months(p.months),
            current_price=p.current_price,
            definition=PlanDefinition.model_validate(p.definition),
        )
        for p in db.scalars(
            select(SubscriptionPeriod)
            .where(
                SubscriptionPeriod.project_id == staff.project_id,
                SubscriptionPeriod.ends_at > now,
            )
            .order_by(SubscriptionPeriod.starts_at)
        )
    ]
    start, end = now, add_months(now, months)
    anchor = now.day
    resulting_seats = definition.seats + extras
    additional_ai = 0
    expires = now + timedelta(minutes=15)
    if request.kind == "subscribe":
        if current is not None:
            raise conflict("已有付费套餐，请使用续费或升级")
        extras = 0
        resulting_seats = definition.seats
        amount = definition.price(months)
    elif request.kind == "renew":
        if current is None or current.code != plan.code:
            raise conflict("续费必须选择原套餐")
        if account.billing_months != months:
            raise conflict("首版不支持切换月付或年付")
        # Expired subscribers retain the advertised seat count on renewal.
        extras = max(
            extras,
            max(
                0,
                account.seat_limit
                - PlanDefinition.model_validate(current.definition).seats,
            ),
        )
        resulting_seats = definition.seats + extras
        start = utc(account.expires_at) if active and account.expires_at else now
        anchor = account.anchor_day if active and account.anchor_day else start.day
        end = add_months(start, months, anchor_day=anchor)
        amount = definition.price(months) + extras * definition.seat_price(months)
    else:
        if (not active or current is None or account.expires_at is None
                or (not periods and not (operator_override and request.kind == "ai_pack"))):
            raise conflict("需要有效付费套餐才能加购或升级", "SUBSCRIPTION_REQUIRED")
        if account.billing_months != months:
            raise conflict("请使用当前套餐的付费周期")
        end = utc(account.expires_at)
        anchor = account.anchor_day or now.day
        if request.kind == "upgrade":
            for period in periods:
                old = period.definition
                if (
                    definition.rank <= old.rank
                    or definition.seats < old.seats
                    or definition.monthly_ai < old.monthly_ai
                    or definition.knowledge_bytes < old.knowledge_bytes
                    or (definition.channel_limit is not None and (
                        old.channel_limit is None or definition.channel_limit < old.channel_limit
                    ))
                ):
                    raise conflict("只能升级到坐席和额度不减少的档位")
            amount = prorated_charge(
                [
                    (definition.price(p.months) - p.current_price, p.start, p.end)
                    for p in periods
                ],
                now,
            )
            for period in periods:
                if period.start <= now < period.end:
                    for index in range(period.months):
                        month_start = add_months(period.start, index, anchor_day=anchor)
                        month_end = min(
                            period.end,
                            add_months(period.start, index + 1, anchor_day=anchor),
                        )
                        if month_start <= now < month_end:
                            additional_ai = int(
                                (definition.monthly_ai - period.definition.monthly_ai)
                                * remaining_fraction(month_start, month_end, now)
                            )
                            expires = min(expires, month_end)
                            break
        else:
            if plan.id != current.id:
                raise conflict("加购必须使用当前套餐")
            if request.kind == "seats":
                resulting_seats = account.seat_limit + request.quantity
                higher = next_add_on_tier(db, plan.code, definition, "seats")
                if higher is not None and resulting_seats >= higher.seats:
                    raise conflict(
                        f"加购后的席位数将达到「{higher.name}」，请升级套餐",
                        "ADD_ON_UPGRADE_REQUIRED",
                    )
                amount = prorated_charge(
                    [
                        (
                            p.definition.seat_price(p.months) * request.quantity,
                            p.start,
                            p.end,
                        )
                        for p in periods
                    ],
                    now,
                )
            else:
                amount = definition.ai_pack_price * request.quantity
                additional_ai = definition.ai_pack_replies * request.quantity
                month_start, end = ai_pack_window(periods, anchor, now, end)
                expires = min(expires, end)
                resulting_seats = account.seat_limit
                higher = next_add_on_tier(db, plan.code, definition, "monthly_ai")
                if higher is not None:
                    check_ai_add_on(
                        db, staff.project_id, definition, higher, month_start,
                        additional_ai, now,
                    )
        expires = min(expires, utc(account.expires_at))
    used, reserved = seat_usage(db, staff.project_id)
    if resulting_seats < used + reserved:
        raise conflict("所选套餐不足以容纳已启用和已预留的人工席位", "SEAT_LIMIT")
    if amount > 100000000:
        raise conflict("单笔金额超过限额，请减少数量")
    details = QuoteDetails(
        kind=request.kind,
        plan_id=plan.id,
        plan_code=plan.code,
        definition=definition,
        months=months,
        quantity=request.quantity,
        quoted_at=now,
        subscription_expires_at=account.expires_at,
        starts_at=start,
        ends_at=end,
        periods=periods,
        extra_seats=extras,
        resulting_seats=resulting_seats,
        additional_ai=additional_ai,
        anchor_day=anchor,
    )
    quote = BillingQuote(
        project_id=staff.project_id,
        staff_id=staff.id,
        base_version=account.version,
        amount=amount,
        details=details.model_dump(mode="json"),
        expires_at=expires,
    )
    db.add(quote)
    db.flush()
    return quote
