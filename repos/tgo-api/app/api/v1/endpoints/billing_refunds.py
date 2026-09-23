"""Operators approve concrete refund previews; callbacks confirm final money movement."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.endpoints.company_membership import require_company_features
from app.api.v1.endpoints.operations import require_operator
from app.core.database import get_db
from app.core.exceptions import TGOAPIException
from app.models.billing import BillingAudit, BillingRefund
from app.models.platform_operator import PlatformOperator
from app.schemas.billing_refunds import RefundConfirm, RefundCreate, RefundResponse
from app.services.billing_quotes import conflict
from app.services.billing_refunds import (
    confirm_refund,
    preview_refund,
    record_refund_result,
)
from app.services.company_membership import lock_company
from app.services.wechat_pay_client import WeChatPayClient

router = APIRouter(
    dependencies=[Depends(require_company_features), Depends(require_operator)]
)
callback_router = APIRouter(dependencies=[Depends(require_company_features)])


@router.get("/refunds", response_model=list[RefundResponse])
def refunds(
    offset: int = Query(0, ge=0), db: Session = Depends(get_db)
) -> list[BillingRefund]:
    return list(
        db.scalars(
            select(BillingRefund)
            .order_by(BillingRefund.created_at.desc())
            .offset(offset)
            .limit(100)
        )
    )


@router.post("/refunds/preview", response_model=RefundResponse)
def preview(
    payload: RefundCreate,
    db: Session = Depends(get_db),
    operator: PlatformOperator = Depends(require_operator),
) -> BillingRefund:
    result = preview_refund(db, operator, payload)
    db.commit()
    return result


@router.post("/refunds/{identifier}/confirm", response_model=RefundResponse)
def confirm(
    identifier: UUID,
    payload: RefundConfirm,
    db: Session = Depends(get_db),
    operator: PlatformOperator = Depends(require_operator),
) -> BillingRefund:
    result = confirm_refund(db, operator, identifier)
    db.commit()
    return result


@router.delete("/refunds/{identifier}", status_code=204)
def cancel(
    identifier: UUID,
    db: Session = Depends(get_db),
    operator: PlatformOperator = Depends(require_operator),
) -> Response:
    refund = db.get(BillingRefund, identifier)
    if refund is None:
        raise conflict("退款预览不存在")
    lock_company(db, refund.project_id)
    db.refresh(refund)
    if refund.status != "draft":
        raise conflict("仅未提交的退款预览可以取消")
    refund.status = "cancelled"
    db.add(
        BillingAudit(
            operator_id=operator.id,
            project_id=refund.project_id,
            action="refund.cancel",
            reason="运营取消未提交的退款预览",
            detail={"refund_id": str(refund.id)},
        )
    )
    db.commit()
    return Response(status_code=204)


@callback_router.post("/payments/wechat/refund-notify", status_code=204)
async def refund_notify(request: Request, db: Session = Depends(get_db)) -> Response:
    body = bytearray()
    async for part in request.stream():
        body.extend(part)
        if len(body) > 131072:
            raise TGOAPIException(
                "通知体积超限", code="INVALID_REFUND_NOTIFICATION", status_code=400
            )
    try:
        result = WeChatPayClient().decode_refund_notification(
            bytes(body), dict(request.headers)
        )
    except TGOAPIException:
        raise
    except Exception as exc:
        raise TGOAPIException(
            "退款通知验证失败", code="INVALID_REFUND_NOTIFICATION", status_code=400
        ) from exc
    record_refund_result(db, result)
    db.commit()
    return Response(status_code=204)
