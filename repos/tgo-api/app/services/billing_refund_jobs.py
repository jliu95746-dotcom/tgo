"""Idempotent refund submission and verified query recovery."""

from uuid import UUID

from app.core.database import SessionLocal
from app.models.billing import BillingOrder, BillingRefund
from app.services.billing_refunds import record_refund_result
from app.services.wechat_pay_client import WeChatPayClient


def process_refund(identifier: UUID) -> str:
    with SessionLocal() as db:
        refund = db.get(BillingRefund, identifier)
        if refund is None:
            raise ValueError("Refund job references a missing refund")
        if refund.status == "success":
            return "done"
        if refund.status in {"closed", "abnormal"}:
            return "review"
        if refund.status not in {"pending", "processing"}:
            raise ValueError("Refund is not confirmed")
        order = db.get(BillingOrder, refund.order_id)
        assert order is not None
        number, order_number, total, amount, reason = (
            refund.number,
            order.number,
            order.amount,
            refund.amount,
            refund.reason,
        )
        known = refund.provider_id is not None
    client = WeChatPayClient()
    if known:
        result = client.query_refund(number)
    else:
        try:
            result = client.request_refund(order_number, number, total, amount, reason)
        except Exception:
            # The provider may have accepted a request before the connection failed.
            # If lookup is also unavailable, the durable job retries the same number.
            result = client.query_refund(number)
    if result.out_refund_no != number or result.out_trade_no != order_number:
        raise ValueError("Provider refund result does not match the requested order")
    with SessionLocal() as db:
        refund = record_refund_result(db, result)
        db.commit()
        return (
            "done"
            if refund.status == "success"
            else "review"
            if refund.status in {"closed", "abnormal"}
            else "pending"
        )
