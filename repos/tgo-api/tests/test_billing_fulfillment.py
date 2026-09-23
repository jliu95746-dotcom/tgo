"""Paid orders can be replayed safely, and stale quotes stay traceable."""

from datetime import timedelta

from app.models.billing import BillingOrder
from app.models.company_account import AICreditBatch
from app.schemas.billing import QuoteRequest
from app.services.billing_fulfillment import fulfill_order
from app.services.billing_quotes import create_quote
from tests.test_billing_quotes import commercial_company  # noqa: F401


def paid_order(db, staff, quote, now):
    BillingOrder.__table__.create(db.get_bind(), checkfirst=True)
    AICreditBatch.__table__.create(db.get_bind(), checkfirst=True)
    order = BillingOrder(
        project_id=staff.project_id,
        quote_id=quote.id,
        amount=quote.amount,
        payment_status="paid",
        paid_at=now,
        expires_at=quote.expires_at,
    )
    db.add(order)
    db.commit()
    return order


def test_repeated_fulfillment_only_grants_one_month(commercial_company):
    db, staff, plan, account, now = commercial_company
    quote = create_quote(
        db, staff, QuoteRequest(kind="subscribe", plan_id=plan.id, months=12), now
    )
    order = paid_order(db, staff, quote, now)
    fulfill_order(db, order.id, now)
    db.commit()
    version = account.version
    fulfill_order(db, order.id, now + timedelta(minutes=1))
    db.commit()
    assert account.version == version
    assert order.fulfillment_status == "applied"
    assert db.query(AICreditBatch).count() == 1
    assert db.query(AICreditBatch).one().remaining == 100
    assert account.expires_at.year == 2027


def test_paid_stale_quote_retains_payment_without_granting(commercial_company):
    db, staff, plan, account, now = commercial_company
    quote = create_quote(
        db, staff, QuoteRequest(kind="subscribe", plan_id=plan.id), now
    )
    order = paid_order(db, staff, quote, now)
    account.version += 1
    db.commit()
    fulfill_order(db, order.id, now)
    db.commit()
    assert order.payment_status == "paid"
    assert order.fulfillment_status == "conflict"
    assert db.query(AICreditBatch).count() == 0


def test_prepaid_renewal_preserves_expiry_and_does_not_issue_future_quota(
    commercial_company,
):
    db, staff, plan, account, now = commercial_company
    quote = create_quote(
        db, staff, QuoteRequest(kind="subscribe", plan_id=plan.id), now
    )
    order = paid_order(db, staff, quote, now)
    fulfill_order(db, order.id, now)
    db.commit()
    quote = create_quote(db, staff, QuoteRequest(kind="renew", plan_id=plan.id), now)
    order = paid_order(db, staff, quote, now)
    fulfill_order(db, order.id, now)
    db.commit()
    assert account.expires_at.month == 3 and account.expires_at.day == 16
    assert db.query(AICreditBatch).count() == 1
