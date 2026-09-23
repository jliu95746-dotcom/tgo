"""Refunds require review and only verified success affects money and service."""

from datetime import datetime, timezone

import pytest

from app.core.exceptions import TGOAPIException
from app.models.billing import BillingRefund, BillingJob
from app.models.company_account import AICreditBatch
from app.schemas.billing_refunds import RefundCreate, ProviderRefund, RefundAmount
from app.services.billing_refunds import (
    preview_refund,
    confirm_refund,
    record_refund_result,
)
from tests.test_billing_quotes import commercial_company  # noqa: F401
from tests.test_billing_support import prepare


def refund_fixture(fixture):
    db, staff, order, operator, account = prepare(fixture)
    for model in (BillingRefund, BillingJob, AICreditBatch):
        model.__table__.create(db.get_bind())
    order.paid_at = datetime.now(timezone.utc)
    order.transaction_id = "synthetic-payment"
    db.commit()
    return db, staff, order, operator, account


def test_refund_preview_does_not_move_money_and_success_only_applies_once(
    commercial_company,
):
    db, _, order, operator, account = refund_fixture(commercial_company)
    refund = preview_refund(
        db,
        operator,
        RefundCreate(
            order_id=order.id,
            amount=1000,
            reason="合成售后人工复核退款",
            entitlement_action="suspend",
        ),
    )
    db.commit()
    assert (
        refund.status == "draft"
        and order.refunded_amount == 0
        and account.status == "trial"
    )
    confirm_refund(db, operator, refund.id)
    db.commit()
    confirm_refund(db, operator, refund.id)
    db.commit()
    assert db.query(BillingJob).count() == 1
    result = ProviderRefund(
        refund_id="synthetic-refund",
        out_refund_no=refund.number,
        out_trade_no=order.number,
        transaction_id=order.transaction_id,
        status="SUCCESS",
        success_time=datetime.now(timezone.utc),
        amount=RefundAmount(total=3000, refund=1000),
    )
    record_refund_result(db, result)
    db.commit()
    record_refund_result(db, result)
    db.commit()
    assert order.refunded_amount == 1000 and account.status == "suspended"


def test_pending_refunds_reserve_refundable_amount(commercial_company):
    db, _, order, operator, _ = refund_fixture(commercial_company)
    payload = RefundCreate(
        order_id=order.id, amount=2000, reason="合成售后人工复核退款", entitlement_action="keep"
    )
    preview_refund(db, operator, payload)
    db.commit()
    with pytest.raises(TGOAPIException):
        preview_refund(db, operator, payload)
    assert db.query(BillingRefund).count() == 1


def test_wrong_refund_amount_does_not_change_original_order(commercial_company):
    db, _, order, operator, _ = refund_fixture(commercial_company)
    refund = preview_refund(
        db,
        operator,
        RefundCreate(
            order_id=order.id,
            amount=1000,
            reason="合成售后人工复核退款",
            entitlement_action="keep",
        ),
    )
    db.commit()
    result = ProviderRefund(
        refund_id="synthetic-refund",
        out_refund_no=refund.number,
        out_trade_no=order.number,
        transaction_id=order.transaction_id,
        status="SUCCESS",
        success_time=datetime.now(timezone.utc),
        amount=RefundAmount(total=3000, refund=1001),
    )
    with pytest.raises(TGOAPIException):
        record_refund_result(db, result)
    assert order.refunded_amount == 0 and refund.succeeded_at is None
