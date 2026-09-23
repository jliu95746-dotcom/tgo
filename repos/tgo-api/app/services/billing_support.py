"""Traceable human handling of invoices, suspensions and ambiguous AI receipts."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Staff
from app.models.ai_usage import AIUsageReservation
from app.models.billing import BillingAudit, BillingOrder, InvoiceRequest
from app.models.platform_operator import PlatformOperator
from app.schemas.billing_support import (
    CompanyStateChange,
    InvoiceCreate,
    InvoiceProcess,
    QuotaResolve,
)
from app.services import ai_usage
from app.services.billing_quotes import conflict
from app.services.company_email import utc
from app.services.company_membership import lock_company


def request_invoice(
    db: Session, actor: Staff, payload: InvoiceCreate
) -> InvoiceRequest:
    lock_company(db, actor.project_id, actor)
    order = db.get(BillingOrder, payload.order_id)
    if order is None or order.project_id != actor.project_id:
        raise conflict("订单不存在", "ORDER_NOT_FOUND")
    if order.payment_status != "paid" or order.amount <= order.refunded_amount:
        raise conflict("该订单暂无可开票金额")
    existing = db.scalar(
        select(InvoiceRequest).where(InvoiceRequest.order_id == order.id)
    )
    if existing is not None:
        return existing
    result = InvoiceRequest(project_id=actor.project_id, **payload.model_dump())
    db.add(result)
    db.flush()
    return result


def process_invoice(
    db: Session, operator: PlatformOperator, invoice_id: UUID, payload: InvoiceProcess
) -> InvoiceRequest:
    preview = db.get(InvoiceRequest, invoice_id)
    if preview is None:
        raise conflict("发票申请不存在")
    lock_company(db, preview.project_id)
    db.refresh(preview)
    if preview.status != "requested":
        raise conflict("发票申请已处理，请刷新")
    if payload.status == "issued" and not payload.invoice_number:
        raise conflict("请填写实际开票号码")
    preview.status = payload.status
    preview.invoice_number = (
        payload.invoice_number if payload.status == "issued" else None
    )
    db.add(
        BillingAudit(
            operator_id=operator.id,
            project_id=preview.project_id,
            action=f"invoice.{payload.status}",
            reason=payload.reason,
            detail={
                "invoice_id": str(preview.id),
                "order_id": str(preview.order_id),
                "invoice_number": preview.invoice_number,
            },
        )
    )
    db.flush()
    return preview


def change_company_state(
    db: Session,
    operator: PlatformOperator,
    project_id: UUID,
    payload: CompanyStateChange,
) -> None:
    account = lock_company(db, project_id)
    if account is None:
        raise conflict("现有企业尚未迁入订阅管理，不能自动变更")
    before = account.status
    if payload.action == "suspend":
        if before == "suspended":
            return
        account.status = "suspended"
    else:
        if account.status != "suspended":
            raise conflict("只有停用的企业可以恢复")
        if account.expires_at is None:
            account.status = "pending"
        elif utc(account.expires_at) <= datetime.now(timezone.utc):
            account.status = "expired"
        else:
            account.status = "active" if account.plan_id else "trial"
    account.version += 1
    db.add(
        BillingAudit(
            operator_id=operator.id,
            project_id=project_id,
            action=f"company.{payload.action}",
            reason=payload.reason,
            detail={"before": before, "after": account.status},
        )
    )
    db.flush()


def resolve_quota(
    db: Session, operator: PlatformOperator, identifier: UUID, payload: QuotaResolve
) -> None:
    row = db.get(AIUsageReservation, identifier)
    if row is None:
        raise conflict("用量记录不存在")
    row = ai_usage.owned(db, row.project_id, row.id, row.lease_id)
    if row.status != "review":
        raise conflict("只有待核对记录可以人工处理")
    if payload.action == "settle":
        ai_usage.settle(db, row.project_id, row.id, row.lease_id)
    else:
        ai_usage.release(
            db, row.project_id, row.id, row.lease_id, delivery_rejected=True
        )
    db.add(
        BillingAudit(
            operator_id=operator.id,
            project_id=row.project_id,
            action=f"quota.{payload.action}",
            reason=payload.reason,
            detail={"reservation_id": str(row.id), "round_key": row.round_key},
        )
    )
    db.flush()
