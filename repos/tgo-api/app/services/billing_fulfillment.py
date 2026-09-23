"""Idempotent entitlement application under the common company transaction lock."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.billing import BillingOrder, BillingQuote, SeatAddon, SubscriptionPeriod
from app.models.company_account import AICreditBatch
from app.schemas.billing import PlanDefinition, QuoteDetails
from app.services.billing_calendar import add_months
from app.services.billing_quotes import conflict
from app.services.company_email import utc
from app.services.company_membership import lock_company, seat_usage


def grant_credit(
    db: Session,
    project_id: UUID,
    order_id: UUID,
    source: str,
    kind: str,
    amount: int,
    expires: datetime,
) -> None:
    if (
        amount <= 0
        or db.scalar(select(AICreditBatch.id).where(AICreditBatch.source_key == source))
        is not None
    ):
        return
    db.add(
        AICreditBatch(
            project_id=project_id,
            order_id=order_id,
            source_key=source,
            kind=kind,
            amount=amount,
            remaining=amount,
            expires_at=expires,
        )
    )
    db.flush()


def grant_due_month(db: Session, period: SubscriptionPeriod, now: datetime) -> None:
    """Only the current monthly bucket is issued; missed expired buckets are useless."""
    definition = PlanDefinition.model_validate(period.definition)
    for index in range(period.months):
        start = add_months(utc(period.starts_at), index, anchor_day=period.anchor_day)
        end = min(
            utc(period.ends_at),
            add_months(utc(period.starts_at), index + 1, anchor_day=period.anchor_day),
        )
        if start <= now < end:
            grant_credit(
                db,
                period.project_id,
                period.order_id,
                f"month:{period.id}:{index}",
                "plan",
                definition.monthly_ai,
                end,
            )
            return


def fulfill_order(
    db: Session, order_id: UUID, now: datetime | None = None
) -> BillingOrder:
    now = now or datetime.now(timezone.utc)
    preview = db.get(BillingOrder, order_id)
    if preview is None:
        raise conflict("订单不存在")
    account = lock_company(db, preview.project_id)
    order = db.scalar(
        select(BillingOrder)
        .where(BillingOrder.id == order_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert order is not None
    if order.fulfillment_status == "applied":
        return order
    if order.payment_status != "paid" or order.paid_at is None:
        raise conflict("订单尚未付款")
    quote = db.get(BillingQuote, order.quote_id)
    assert quote is not None
    details = QuoteDetails.model_validate(quote.details)
    paid = utc(order.paid_at)
    if (
        account is None
        or account.status in {"pending", "suspended"}
        or account.version != quote.base_version
        or paid > utc(quote.expires_at)
        or order.refunded_amount
    ):
        order.fulfillment_status = "conflict"
        order.failure_code = "SUBSCRIPTION_BASE_CHANGED"
        return order
    used, reserved = seat_usage(db, order.project_id)
    if details.resulting_seats < used + reserved:
        order.fulfillment_status = "conflict"
        order.failure_code = "SEAT_USAGE_CHANGED"
        return order
    if details.kind in {"subscribe", "renew"}:
        early = (
            details.kind == "renew"
            and account.expires_at is not None
            and utc(account.expires_at) > paid
        )
        start = utc(account.expires_at) if early and account.expires_at else paid
        anchor = details.anchor_day if early else start.day
        end = add_months(start, details.months, anchor_day=anchor)
        period = SubscriptionPeriod(
            project_id=order.project_id,
            order_id=order.id,
            plan_id=details.plan_id,
            starts_at=start,
            ends_at=end,
            months=details.months,
            anchor_day=anchor,
            original_price=details.definition.price(details.months),
            current_price=details.definition.price(details.months),
            original_definition=details.definition.model_dump(mode="json"),
            definition=details.definition.model_dump(mode="json"),
        )
        db.add(period)
        db.flush()
        if details.extra_seats:
            db.add(
                SeatAddon(
                    project_id=order.project_id,
                    order_id=order.id,
                    quantity=details.extra_seats,
                    starts_at=start,
                    ends_at=end,
                )
            )
        if details.kind == "subscribe":
            # End trial buckets without rewriting their historical amount.
            for batch in db.scalars(
                select(AICreditBatch).where(
                    AICreditBatch.project_id == order.project_id,
                    AICreditBatch.kind == "trial",
                    AICreditBatch.expires_at > paid,
                )
            ):
                batch.expires_at = paid
        account.status = "active"
        account.billing_months = details.months
        account.anchor_day = anchor
        account.expires_at = end
        if not early:
            account.plan_id = details.plan_id
            account.started_at = start
            account.seat_limit = details.resulting_seats
        grant_due_month(db, period, now)
    elif details.kind == "upgrade":
        for snapshot in details.periods:
            saved_period = db.get(SubscriptionPeriod, snapshot.id)
            if saved_period is None or saved_period.project_id != order.project_id:
                raise conflict("订阅周期缺失，需人工核对")
            period = saved_period
            # Materialize the old monthly bucket before replacing its definition.
            grant_due_month(db, period, details.quoted_at)
            period.plan_id = details.plan_id
            period.definition = details.definition.model_dump(mode="json")
            period.current_price = details.definition.price(period.months)
            grant_due_month(db, period, now)
            for index in range(period.months):
                start = add_months(
                    utc(period.starts_at), index, anchor_day=period.anchor_day
                )
                end = min(
                    utc(period.ends_at),
                    add_months(
                        utc(period.starts_at), index + 1, anchor_day=period.anchor_day
                    ),
                )
                if start <= details.quoted_at < end:
                    grant_credit(
                        db,
                        order.project_id,
                        order.id,
                        f"upgrade:{order.id}",
                        "plan",
                        details.additional_ai,
                        end,
                    )
        account.plan_id = details.plan_id
        account.seat_limit = details.resulting_seats
    elif details.kind == "seats":
        db.add(
            SeatAddon(
                project_id=order.project_id,
                order_id=order.id,
                quantity=details.quantity,
                starts_at=paid,
                ends_at=details.ends_at,
            )
        )
        account.seat_limit = details.resulting_seats
    else:
        grant_credit(
            db,
            order.project_id,
            order.id,
            f"pack:{order.id}",
            "pack",
            details.additional_ai,
            add_months(paid, 12),
        )
    account.version += 1
    order.fulfillment_status = "applied"
    order.fulfilled_at = now
    order.failure_code = None
    db.flush()
    return order
