"""Refund money and service disposition are recorded only after verified success."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.billing import (
    BillingAudit,
    BillingJob,
    BillingOrder,
    BillingQuote,
    BillingRefund,
)
from app.models.company_account import AICreditBatch
from app.models.platform_operator import PlatformOperator
from app.schemas.billing_refunds import ProviderRefund, RefundCreate, RefundDisposition
from app.services.billing_quotes import conflict
from app.services.company_email import utc
from app.services.company_membership import lock_company


def preview_refund(
    db: Session, operator: PlatformOperator, payload: RefundCreate
) -> BillingRefund:
    order = db.get(BillingOrder, payload.order_id)
    if order is None:
        raise conflict("订单不存在")
    account = lock_company(db, order.project_id)
    db.refresh(order)
    if order.payment_status != "paid" or not order.transaction_id or not order.paid_at:
        raise conflict("订单尚未完成微信付款")
    if utc(order.paid_at) < datetime.now(timezone.utc) - timedelta(days=365):
        raise conflict("订单超过微信原路退款期限，请人工处理售后")
    pending = (
        db.scalar(
            select(func.coalesce(func.sum(BillingRefund.amount), 0)).where(
                BillingRefund.order_id == order.id,
                BillingRefund.status.in_(
                    ["draft", "pending", "processing", "abnormal"]
                ),
            )
        )
        or 0
    )
    count = (
        db.scalar(
            select(func.count())
            .select_from(BillingRefund)
            .where(
                BillingRefund.order_id == order.id, BillingRefund.status != "cancelled"
            )
        )
        or 0
    )
    if count >= 50 or payload.amount > order.amount - order.refunded_amount - pending:
        raise conflict("可退款金额或退款次数不足，请先核对已有退款")
    quote = db.get(BillingQuote, order.quote_id)
    assert quote is not None
    remaining, granted = db.execute(
        select(
            func.coalesce(func.sum(AICreditBatch.remaining), 0),
            func.coalesce(func.sum(AICreditBatch.amount), 0),
        ).where(AICreditBatch.order_id == order.id)
    ).one()
    disposition = RefundDisposition(
        action=payload.entitlement_action,
        original_order_amount=order.amount,
        already_refunded=order.refunded_amount,
        available_ai_from_order=remaining,
        granted_ai_from_order=granted,
        company_status=account.status if account else "legacy",
        subscription_version=account.version if account else None,
        order_kind=str(quote.details["kind"]),
    )
    if payload.entitlement_action == "suspend" and account is None:
        raise conflict("现有企业尚未迁移，不能自动暂停")
    result = BillingRefund(
        project_id=order.project_id,
        order_id=order.id,
        operator_id=operator.id,
        amount=payload.amount,
        reason=payload.reason,
        status="draft",
        disposition=disposition.model_dump(mode="json"),
    )
    db.add(result)
    db.flush()
    return result


def confirm_refund(
    db: Session, operator: PlatformOperator, identifier: UUID
) -> BillingRefund:
    refund = db.get(BillingRefund, identifier)
    if refund is None:
        raise conflict("退款预览不存在")
    account = lock_company(db, refund.project_id)
    db.refresh(refund)
    if refund.status != "draft":
        return refund
    preview = RefundDisposition.model_validate(refund.disposition)
    if (account.version if account else None) != preview.subscription_version:
        raise conflict("订阅状态已变化，请取消此预览后重新核对")
    refund.status = "pending"
    db.add(
        BillingJob(
            business_key=f"refund:{refund.id}",
            project_id=refund.project_id,
            order_id=refund.order_id,
            kind="refund",
            available_at=datetime.now(timezone.utc),
        )
    )
    db.add(
        BillingAudit(
            operator_id=operator.id,
            project_id=refund.project_id,
            action="refund.confirm",
            reason=refund.reason,
            detail={
                "refund_id": str(refund.id),
                "amount": refund.amount,
                "entitlement_action": preview.action,
            },
        )
    )
    db.flush()
    return refund


def record_refund_result(db: Session, result: ProviderRefund) -> BillingRefund:
    refund = db.scalar(
        select(BillingRefund).where(BillingRefund.number == result.out_refund_no)
    )
    if refund is None:
        raise conflict("微信退款流水尚未关联本地退款申请", "REFUND_UNKNOWN")
    account = lock_company(db, refund.project_id)
    db.refresh(refund)
    order = db.get(BillingOrder, refund.order_id)
    assert order is not None
    db.refresh(order)
    if (
        result.out_trade_no != order.number
        or result.transaction_id != order.transaction_id
        or result.amount.refund != refund.amount
        or result.amount.total != order.amount
        or result.amount.currency != "CNY"
        or (refund.provider_id and refund.provider_id != result.refund_id)
    ):
        raise conflict("退款通知与原订单不匹配", "REFUND_MISMATCH")
    if refund.succeeded_at is not None:
        return refund
    refund.provider_id = result.refund_id
    if result.status != "SUCCESS":
        refund.status = result.status.lower()
        return refund
    if result.success_time is None or result.success_time.tzinfo is None:
        raise conflict("退款成功时间缺失")
    if order.refunded_amount + refund.amount > order.amount:
        raise conflict("累计退款金额异常", "REFUND_AMOUNT_CONFLICT")
    order.refunded_amount += refund.amount
    refund.status = "success"
    refund.succeeded_at = result.success_time
    preview = RefundDisposition.model_validate(refund.disposition)
    if preview.action == "suspend" and account is not None:
        account.status = "suspended"
        account.version += 1
    db.add(
        BillingAudit(
            operator_id=refund.operator_id,
            project_id=refund.project_id,
            action="refund.success",
            reason=refund.reason,
            detail={
                "refund_id": str(refund.id),
                "amount": refund.amount,
                "entitlement_action": preview.action,
                "provider_id": result.refund_id,
            },
        )
    )
    db.flush()
    return refund
