"""Order creation and verified payment persistence; no network inside a DB lock."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Staff
from app.models.billing import BillingJob, BillingOrder, BillingQuote, PaymentEvent
from app.services.billing_quotes import conflict
from app.services.company_email import utc
from app.services.company_membership import lock_company


def create_order(
    db: Session, actor: Staff, quote_id: UUID, now: datetime | None = None
) -> BillingOrder:
    now = now or datetime.now(timezone.utc)
    account = lock_company(db, actor.project_id, actor)
    quote = db.get(BillingQuote, quote_id)
    if quote is None or quote.project_id != actor.project_id:
        raise conflict("报价不存在", "QUOTE_NOT_FOUND")
    existing = db.scalar(select(BillingOrder).where(BillingOrder.quote_id == quote.id))
    if existing is not None:
        return existing
    if (
        account is None
        or account.version != quote.base_version
        or utc(quote.expires_at) <= now
    ):
        raise conflict("报价已失效，请重新确认价格", "QUOTE_EXPIRED")
    order = BillingOrder(
        project_id=actor.project_id,
        quote_id=quote.id,
        amount=quote.amount,
        expires_at=quote.expires_at,
    )
    db.add(order)
    db.flush()
    if order.amount == 0:
        order.payment_status = "paid"
        order.paid_at = now
        enqueue_fulfillment(db, order, now)
    return order


def enqueue_fulfillment(db: Session, order: BillingOrder, now: datetime) -> None:
    key = f"fulfill:{order.id}"
    if db.scalar(select(BillingJob.id).where(BillingJob.business_key == key)) is None:
        db.add(
            BillingJob(
                business_key=key,
                project_id=order.project_id,
                order_id=order.id,
                kind="fulfill",
                available_at=now,
            )
        )
        db.flush()


def record_verified_payment(
    db: Session,
    *,
    number: str,
    transaction_id: str,
    amount: int,
    currency: str,
    paid_at: datetime,
    event_key: str,
) -> BillingOrder:
    """Called only after provider signature, merchant and AppID validation."""
    preview = db.scalar(select(BillingOrder).where(BillingOrder.number == number))
    if preview is None:
        raise conflict("支付订单不存在", "PAYMENT_ORDER_UNKNOWN")
    lock_company(db, preview.project_id)
    order = db.scalar(
        select(BillingOrder)
        .where(BillingOrder.id == preview.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert order is not None
    if currency != "CNY" or amount != order.amount or amount <= 0:
        raise conflict("支付金额或币种不符", "PAYMENT_AMOUNT_MISMATCH")
    if order.transaction_id is not None and order.transaction_id != transaction_id:
        raise conflict("支付流水不符", "PAYMENT_TRANSACTION_MISMATCH")
    other = db.scalar(
        select(BillingOrder.id).where(
            BillingOrder.transaction_id == transaction_id, BillingOrder.id != order.id
        )
    )
    if other is not None:
        raise conflict("支付流水已关联其他订单", "PAYMENT_TRANSACTION_MISMATCH")
    event = db.scalar(select(PaymentEvent).where(PaymentEvent.event_key == event_key))
    if event is not None and (
        event.order_id != order.id or event.transaction_id != transaction_id
    ):
        raise conflict("支付事件不符", "PAYMENT_EVENT_MISMATCH")
    if event is None:
        db.add(
            PaymentEvent(
                event_key=event_key,
                project_id=order.project_id,
                order_id=order.id,
                transaction_id=transaction_id,
                amount=amount,
            )
        )
    order.transaction_id = transaction_id
    order.payment_status = "paid"
    if order.paid_at is None:
        order.paid_at = paid_at
    enqueue_fulfillment(db, order, datetime.now(timezone.utc))
    db.flush()
    return order
