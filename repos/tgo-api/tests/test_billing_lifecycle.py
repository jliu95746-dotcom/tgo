"""One company's paid lifecycle preserves time, consumed credits and add-ons."""

from datetime import datetime, timedelta, timezone

import pytest

from app.core.exceptions import TGOAPIException
from app.models.ai_usage import AIUsageMovement, AIUsageReservation
from app.models.billing import (
    BillingJob,
    BillingOrder,
    BillingPlan,
    PaymentEvent,
    SubscriptionPeriod,
)
from app.models.company_account import AICreditBatch
from app.schemas.billing import QuoteRequest
from app.services.ai_usage import begin_publication, require_service, reserve, settle
from app.services.billing_fulfillment import fulfill_order
from app.services.billing_orders import create_order, record_verified_payment
from app.services.billing_quotes import create_quote
from app.services.company_email import utc
from tests.test_billing_quotes import commercial_company  # noqa: F401


def test_paid_company_full_lifecycle(commercial_company):
    db, staff, basic, account, _ = commercial_company
    now = datetime.now(timezone.utc)
    account.expires_at = now + timedelta(days=7)
    for model in (
        BillingOrder,
        PaymentEvent,
        BillingJob,
        AICreditBatch,
        AIUsageReservation,
        AIUsageMovement,
    ):
        model.__table__.create(db.get_bind())
    db.add(
        AICreditBatch(
            project_id=staff.project_id,
            source_key="trial:synthetic",
            kind="trial",
            amount=100,
            remaining=100,
            expires_at=account.expires_at,
        )
    )
    higher = BillingPlan(
        code="higher",
        version=1,
        status="published",
        definition={
            **basic.definition,
            "name": "进阶版",
            "rank": 2,
            "monthly_price": 6000,
            "annual_price": 60000,
            "seats": 5,
            "monthly_ai": 200,
        },
    )
    db.add(higher)
    db.commit()

    def purchase(kind, plan_id=None, quantity=1, instant=now):
        quote = create_quote(
            db,
            staff,
            QuoteRequest(kind=kind, plan_id=plan_id, quantity=quantity, months=1),
            instant,
        )
        order = create_order(db, staff, quote.id, instant)
        db.commit()
        for _ in range(2):
            record_verified_payment(
                db,
                number=order.number,
                transaction_id=f"synthetic-{order.number}",
                amount=order.amount,
                currency="CNY",
                paid_at=instant,
                event_key=f"notify:{order.number}",
            )
            db.commit()
        fulfill_order(db, order.id, instant)
        db.commit()
        version = account.version
        fulfill_order(db, order.id, instant)
        db.commit()
        assert order.fulfillment_status == "applied" and account.version == version
        return order, quote

    purchase("subscribe", basic.id)
    trial = db.query(AICreditBatch).filter_by(kind="trial").one()
    assert utc(trial.expires_at) <= now
    original_end = utc(account.expires_at)
    reply = reserve(db, staff.project_id, "synthetic-first-reply")
    begin_publication(
        db,
        staff.project_id,
        reply.id,
        reply.lease_id,
        {"kind": "im", "client_msg_no": "synthetic"},
    )
    settle(db, staff.project_id, reply.id, reply.lease_id)
    db.commit()
    original_batch = db.get(AICreditBatch, reply.batch_id)
    assert original_batch.remaining == 99

    purchase("renew", basic.id)
    prepaid_end = utc(account.expires_at)
    assert prepaid_end > original_end
    assert db.query(AICreditBatch).filter_by(kind="plan").count() == 1
    assert db.query(SubscriptionPeriod).count() == 2

    purchase("upgrade", higher.id)
    assert utc(account.expires_at) == prepaid_end and account.seat_limit == 5
    assert original_batch.remaining == 99
    upgrade_batch = (
        db.query(AICreditBatch).filter(AICreditBatch.source_key.like("upgrade:%")).one()
    )
    assert upgrade_batch.amount == 100 and utc(upgrade_batch.expires_at) == original_end
    assert all(
        period.definition["monthly_ai"] == 200
        for period in db.query(SubscriptionPeriod)
    )

    purchase("seats", higher.id, quantity=2)
    assert account.seat_limit == 7 and utc(account.expires_at) == prepaid_end
    purchase("ai_pack", higher.id, quantity=2)
    pack = db.query(AICreditBatch).filter_by(kind="pack").one()
    assert pack.remaining == 200 and utc(pack.expires_at) > prepaid_end

    _, renewal_quote = purchase("renew", higher.id)
    assert renewal_quote.details["resulting_seats"] == 7
    assert account.seat_limit == 7
    expired = utc(account.expires_at) + timedelta(hours=1)
    with pytest.raises(TGOAPIException) as error:
        require_service(account, expired)
    assert error.value.code == "SUBSCRIPTION_EXPIRED"
    assert pack.remaining == 200  # Subscription expiry does not erase a valid pack.

    purchase("renew", higher.id, instant=expired)
    require_service(account, expired)
    assert utc(account.started_at) <= expired < utc(account.expires_at)
    assert pack.remaining == 200 and utc(pack.expires_at) > expired
    assert db.query(BillingJob).count() == db.query(BillingOrder).count() == 7
    assert db.query(PaymentEvent).count() == 7
