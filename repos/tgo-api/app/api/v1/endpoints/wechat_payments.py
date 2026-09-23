"""Native checkout and authenticated provider notifications."""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.v1.endpoints.billing import order_detail, require_purchases
from app.api.v1.endpoints.company_membership import require_company_features
from app.core.database import get_db
from app.core.exceptions import TGOAPIException
from app.core.security import require_admin
from app.models import Staff
from app.models.billing import BillingOrder
from app.schemas.billing import OrderResponse
from app.services.billing_orders import record_verified_payment
from app.services.billing_quotes import conflict
from app.services.company_email import utc
from app.services.company_membership import lock_company
from app.services.wechat_pay_client import WeChatPayClient

router = APIRouter(dependencies=[Depends(require_company_features)])


@router.post(
    "/billing/orders/{order_id}/wechat",
    response_model=OrderResponse,
    dependencies=[Depends(require_purchases)],
)
def checkout(
    order_id: UUID,
    db: Session = Depends(get_db),
    actor: Staff = Depends(require_admin()),
) -> BillingOrder:
    lock_company(db, actor.project_id, actor)
    order = order_detail(order_id, db, actor)
    if order.payment_status == "paid" or order.code_url:
        return order
    if order.payment_status != "pending" or utc(order.expires_at) <= datetime.now(
        timezone.utc
    ):
        raise conflict("订单已过期，请重新购买", "ORDER_EXPIRED")
    number, amount, expires = order.number, order.amount, utc(order.expires_at)
    db.commit()
    code_url = WeChatPayClient().native(number, amount, expires)
    lock_company(db, actor.project_id, actor)
    db.refresh(order)
    if order.payment_status == "pending":
        order.code_url = code_url
    db.commit()
    return order


@router.post("/payments/wechat/notify", status_code=204)
async def notify(request: Request, db: Session = Depends(get_db)) -> Response:
    body = bytearray()
    async for part in request.stream():
        body.extend(part)
        if len(body) > 131072:
            raise TGOAPIException(
                "通知体积超限", code="INVALID_PAYMENT_NOTIFICATION", status_code=400
            )
    try:
        event_id, transaction = WeChatPayClient().decode_notification(
            bytes(body), dict(request.headers)
        )
        if (
            transaction.trade_state != "SUCCESS"
            or transaction.transaction_id is None
            or transaction.success_time is None
            or transaction.amount is None
        ):
            raise ValueError("Payment not successful")
    except TGOAPIException:
        raise
    except Exception as exc:
        raise TGOAPIException(
            "支付通知验证失败", code="INVALID_PAYMENT_NOTIFICATION", status_code=400
        ) from exc
    record_verified_payment(
        db,
        number=transaction.out_trade_no,
        transaction_id=transaction.transaction_id,
        amount=transaction.amount.total,
        currency=transaction.amount.currency,
        paid_at=transaction.success_time,
        event_key=f"notify:{event_id}",
    )
    db.commit()
    return Response(status_code=204)
