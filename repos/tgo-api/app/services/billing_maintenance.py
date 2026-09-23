"""Monthly issuance, expiry and payment query compensation independent of the UI."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select

from app.core.database import SessionLocal
from app.models.billing import BillingOrder, SeatAddon, SubscriptionPeriod
from app.models.company_account import CompanyAccount
from app.schemas.billing import PlanDefinition
from app.services.billing_fulfillment import grant_due_month
from app.services.billing_orders import record_verified_payment
from app.services.company_email import utc
from app.services.company_membership import lock_company
from app.services.wechat_pay_client import WeChatPayClient


def maintain_subscriptions() -> None:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        ids = list(
            db.scalars(
                select(CompanyAccount.project_id).where(
                    CompanyAccount.status.in_(["trial", "active"])
                )
            )
        )
    for project_id in ids:
        with SessionLocal() as db:
            account = lock_company(db, project_id)
            if account is None or account.status not in {"trial", "active"}:
                continue
            if account.expires_at is not None and utc(account.expires_at) <= now:
                account.status = "expired"
                # Expiry itself does not change quote basis: a previously paid order
                # remains fulfillable after an outage. API gates use actual timestamps.
            elif account.status == "active":
                period = db.scalar(
                    select(SubscriptionPeriod).where(
                        SubscriptionPeriod.project_id == project_id,
                        SubscriptionPeriod.starts_at <= now,
                        SubscriptionPeriod.ends_at > now,
                    )
                )
                if period is not None:
                    definition = PlanDefinition.model_validate(period.definition)
                    extras = (
                        db.scalar(
                            select(
                                func.coalesce(func.sum(SeatAddon.quantity), 0)
                            ).where(
                                SeatAddon.project_id == project_id,
                                SeatAddon.starts_at <= now,
                                SeatAddon.ends_at > now,
                            )
                        )
                        or 0
                    )
                    if (
                        account.plan_id != period.plan_id
                        or account.seat_limit != definition.seats + extras
                    ):
                        account.plan_id = period.plan_id
                        account.seat_limit = definition.seats + extras
                        account.version += 1
                    grant_due_month(db, period, now)
            db.commit()


def reconcile_one() -> bool:
    now = datetime.now(timezone.utc)
    # Closed orders remain eligible for query to catch delayed provider settlement.
    with SessionLocal() as db:
        order = db.scalar(
            select(BillingOrder)
            .where(
                BillingOrder.payment_status.in_(["pending", "closed"]),
                BillingOrder.amount > 0,
                BillingOrder.created_at > now - timedelta(days=7),
                or_(
                    BillingOrder.last_checked_at.is_(None),
                    BillingOrder.last_checked_at < now - timedelta(minutes=1),
                ),
            )
            .order_by(
                BillingOrder.last_checked_at.asc().nullsfirst(), BillingOrder.created_at
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if order is None:
            return False
        order.last_checked_at = now
        number, expires, order_id, project_id = (
            order.number,
            utc(order.expires_at),
            order.id,
            order.project_id,
        )
        db.commit()
    client = WeChatPayClient()
    transaction = client.query(number)
    if transaction.trade_state == "SUCCESS":
        assert (
            transaction.transaction_id is not None
            and transaction.success_time is not None
            and transaction.amount is not None
        )
        with SessionLocal() as db:
            record_verified_payment(
                db,
                number=number,
                transaction_id=transaction.transaction_id,
                amount=transaction.amount.total,
                currency=transaction.amount.currency,
                paid_at=transaction.success_time,
                event_key=f"query:{transaction.transaction_id}",
            )
            db.commit()
    elif transaction.trade_state in {"CLOSED", "REVOKED"} or (
        transaction.trade_state == "NOTPAY" and expires <= now
    ):
        if transaction.trade_state == "NOTPAY":
            client.close(number)
        with SessionLocal() as db:
            lock_company(db, project_id)
            order = db.get(BillingOrder, order_id)
            if order is not None and order.payment_status == "pending":
                order.payment_status = "closed"
                db.commit()
    return True
