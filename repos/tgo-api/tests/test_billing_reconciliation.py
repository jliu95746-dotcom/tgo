"""Statements recover missing notifications and retain conflicting evidence."""

from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.billing import BillingJob, BillingOrder, BillingRefund, PaymentEvent
from app.models.billing_reconciliation import BillingReconciliation
from app.schemas.billing import QuoteRequest
from app.services import billing_reconciliation as service
from app.services.billing_orders import create_order
from app.services.billing_quotes import create_quote
from app.services.wechat_bill_parser import TradeBillEntry
from app.services.wechat_pay_client import PaymentAmount, PaymentTransaction
from tests.test_billing_quotes import commercial_company  # noqa: F401


@pytest.mark.parametrize("difference", [None, "transaction", "amount", "currency"])
def test_query_compensation_matches_statement(
    commercial_company, monkeypatch, difference  # noqa: F811 - shared pytest fixture
):
    db, staff, plan, _, now = commercial_company
    for model in (BillingOrder, PaymentEvent, BillingJob, BillingReconciliation):
        model.__table__.create(db.get_bind())
    quote = create_quote(
        db, staff, QuoteRequest(kind="subscribe", plan_id=plan.id), now
    )
    order = create_order(db, staff, quote.id, now)
    db.commit()
    number = order.number
    monkeypatch.setattr(service, "SessionLocal", sessionmaker(db.get_bind()))
    monkeypatch.setattr(service.settings, "WECHAT_PAY_MCH_ID", "synthetic-merchant")
    monkeypatch.setattr(service.settings, "WECHAT_PAY_APP_ID", "synthetic-app")
    entry = TradeBillEntry(
        number=number,
        transaction_id="statement-tx",
        merchant_id="synthetic-merchant",
        app_id="synthetic-app",
        state="SUCCESS",
        currency="CNY",
        amount=3000,
        refund_number=None,
    )
    client = Mock()
    client.download_trade_bill.return_value = (
        b"verified synthetic statement",
        "digest",
    )
    client.query.return_value = PaymentTransaction(
        mchid="synthetic-merchant",
        appid="synthetic-app",
        out_trade_no=number,
        trade_state="SUCCESS",
        success_time=now,
        transaction_id="other-tx" if difference == "transaction" else "statement-tx",
        amount=PaymentAmount(
            total=1 if difference == "amount" else 3000,
            currency="USD" if difference == "currency" else "CNY",
        ),
    )
    monkeypatch.setattr(service, "WeChatPayClient", lambda: client)
    monkeypatch.setattr(service, "parse_trade_bill", lambda _: [entry])

    expected = "review" if difference else "done"
    assert service.reconcile_statement(now.date()) == expected
    assert service.reconcile_statement(now.date()) == expected
    client.download_trade_bill.assert_called_once()
    db.expire_all()
    assert order.payment_status == ("pending" if difference else "paid")
    assert db.query(BillingJob).count() == (0 if difference else 1)
    record = db.get(BillingReconciliation, now.date())
    assert record.issue_count == (1 if difference else 0)
    if difference:
        assert record.issues[0]["code"] == "STATEMENT_QUERY_MISMATCH"


@pytest.mark.parametrize("mismatch", ["order", "project"])
def test_refund_statement_cannot_apply_another_order_or_company(
    commercial_company, monkeypatch, mismatch  # noqa: F811
):
    db, staff, plan, _, now = commercial_company
    for model in (BillingOrder, BillingRefund, BillingReconciliation):
        model.__table__.create(db.get_bind())
    quote = create_quote(
        db, staff, QuoteRequest(kind="subscribe", plan_id=plan.id), now
    )
    order = create_order(db, staff, quote.id, now)
    order.payment_status = "paid"
    order.paid_at = now
    order.transaction_id = "synthetic-tx"
    refund = BillingRefund(
        project_id=uuid4() if mismatch == "project" else staff.project_id,
        order_id=uuid4() if mismatch == "order" else order.id,
        operator_id=uuid4(),
        amount=100,
        reason="synthetic",
        disposition={},
    )
    db.add(refund)
    db.commit()
    entry = TradeBillEntry(
        number=order.number,
        transaction_id=order.transaction_id,
        merchant_id="synthetic-mch",
        app_id="synthetic-app",
        state="REFUND",
        currency="CNY",
        amount=order.amount,
        refund_number=refund.number,
    )
    monkeypatch.setattr(service, "SessionLocal", sessionmaker(db.get_bind()))
    monkeypatch.setattr(service.settings, "WECHAT_PAY_MCH_ID", "synthetic-mch")
    monkeypatch.setattr(service.settings, "WECHAT_PAY_APP_ID", "synthetic-app")
    client = Mock()
    client.download_trade_bill.return_value = (b"synthetic", "digest")
    client.query_refund.return_value = Mock(out_refund_no=refund.number)
    record = Mock()
    monkeypatch.setattr(service, "WeChatPayClient", lambda: client)
    monkeypatch.setattr(service, "parse_trade_bill", lambda _: [entry])
    monkeypatch.setattr(service, "record_refund_result", record)
    assert service.reconcile_statement(now.date()) == "review"
    client.query_refund.assert_not_called()
    record.assert_not_called()
    report = db.get(BillingReconciliation, now.date())
    assert report.issue_count == 1
    assert report.issues[0]["code"] == "STATEMENT_REFUND_MISMATCH"
