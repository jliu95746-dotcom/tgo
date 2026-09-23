"""Payment events may repeat without creating another fulfillment job."""

import pytest

from app.core.exceptions import TGOAPIException
from app.models.billing import BillingJob, BillingOrder, PaymentEvent
from app.schemas.billing import QuoteRequest
from app.services.billing_orders import create_order, record_verified_payment
from app.services.billing_quotes import create_quote
from tests.test_billing_quotes import commercial_company  # noqa: F401


def test_repeated_payment_produces_one_durable_job(commercial_company):
    db, staff, plan, _, now = commercial_company
    for model in (BillingOrder, PaymentEvent, BillingJob):
        model.__table__.create(db.get_bind())
    quote = create_quote(
        db, staff, QuoteRequest(kind="subscribe", plan_id=plan.id), now
    )
    order = create_order(db, staff, quote.id, now)
    db.commit()
    assert create_order(db, staff, quote.id, now).id == order.id
    for event in ("notify:synthetic", "notify:synthetic", "query:synthetic"):
        record_verified_payment(
            db,
            number=order.number,
            transaction_id="synthetic-tx",
            amount=3000,
            currency="CNY",
            paid_at=now,
            event_key=event,
        )
        db.commit()
    assert db.query(BillingJob).count() == 1
    assert db.query(PaymentEvent).count() == 2
    assert order.payment_status == "paid" and order.fulfillment_status == "pending"


@pytest.mark.parametrize("amount,currency", [(1, "CNY"), (3000, "USD")])
def test_wrong_amount_or_currency_never_marks_paid(
    commercial_company, amount, currency
):
    db, staff, plan, _, now = commercial_company
    for model in (BillingOrder, PaymentEvent, BillingJob):
        model.__table__.create(db.get_bind())
    quote = create_quote(
        db, staff, QuoteRequest(kind="subscribe", plan_id=plan.id), now
    )
    order = create_order(db, staff, quote.id, now)
    db.commit()
    with pytest.raises(TGOAPIException):
        record_verified_payment(
            db,
            number=order.number,
            transaction_id="bad-tx",
            amount=amount,
            currency=currency,
            paid_at=now,
            event_key="bad-event",
        )
    assert order.payment_status == "pending"
    assert db.query(BillingJob).count() == 0
