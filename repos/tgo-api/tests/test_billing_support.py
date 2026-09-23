"""Invoices and operator actions retain tenant identity and audit evidence."""

from uuid import uuid4

import pytest

from app.core.exceptions import TGOAPIException
from app.models.billing import BillingAudit, BillingOrder, InvoiceRequest
from app.models.platform_operator import PlatformOperator
from app.schemas.billing_support import (
    CompanyStateChange,
    InvoiceCreate,
    InvoiceProcess,
)
from app.schemas.billing import QuoteRequest
from app.services.billing_orders import create_order
from app.services.billing_quotes import create_quote
from app.services.billing_support import (
    request_invoice,
    process_invoice,
    change_company_state,
)
from tests.test_billing_quotes import commercial_company  # noqa: F401


def prepare(fixture):
    db, staff, plan, account, now = fixture
    for model in (BillingOrder, InvoiceRequest, PlatformOperator, BillingAudit):
        model.__table__.create(db.get_bind())
    operator = PlatformOperator(
        email="synthetic-ops@example.com", name="合成运营", password_hash="unused"
    )
    db.add(operator)
    quote = create_quote(
        db, staff, QuoteRequest(kind="subscribe", plan_id=plan.id), now
    )
    order = create_order(db, staff, quote.id, now)
    order.payment_status = "paid"
    order.paid_at = now
    db.commit()
    return db, staff, order, operator, account


def test_invoice_is_idempotent_and_operator_processing_is_audited(commercial_company):
    db, staff, order, operator, _ = prepare(commercial_company)
    payload = InvoiceCreate(
        order_id=order.id,
        title="合成测试企业",
        tax_number="SYNTHETIC1234",
        email="synthetic@example.com",
    )
    first = request_invoice(db, staff, payload)
    db.commit()
    assert request_invoice(db, staff, payload).id == first.id
    process_invoice(
        db,
        operator,
        first.id,
        InvoiceProcess(
            status="issued", invoice_number="synthetic-invoice", reason="测试已核对开票"
        ),
    )
    db.commit()
    assert db.query(BillingAudit).count() == 1
    assert first.status == "issued"
    with pytest.raises(TGOAPIException):
        process_invoice(
            db, operator, first.id, InvoiceProcess(status="rejected", reason="重复处理")
        )


def test_invoice_cannot_reference_another_company_order(commercial_company):
    db, staff, order, _, _ = prepare(commercial_company)
    order.project_id = uuid4()
    db.commit()
    with pytest.raises(TGOAPIException):
        request_invoice(
            db,
            staff,
            InvoiceCreate(
                order_id=order.id,
                title="合成测试企业",
                tax_number="SYNTHETIC1234",
                email="synthetic@example.com",
            ),
        )
    assert db.query(InvoiceRequest).count() == 0


def test_suspended_company_restoration_does_not_extend_expired_trial(
    commercial_company,
):
    db, staff, _, operator, account = prepare(commercial_company)
    previous_expiry = account.expires_at
    change_company_state(
        db,
        operator,
        staff.project_id,
        CompanyStateChange(action="suspend", reason="合成测试暂停企业"),
    )
    db.commit()
    change_company_state(
        db,
        operator,
        staff.project_id,
        CompanyStateChange(action="restore", reason="合成测试恢复企业"),
    )
    db.commit()
    assert account.status == "expired" and account.expires_at == previous_expiry
    assert db.query(BillingAudit).count() == 2
