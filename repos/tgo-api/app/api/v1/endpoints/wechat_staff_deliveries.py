"""Original WeChat device polls human replies after cloud seat assignment."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import Platform, Visitor
from app.schemas.wechat_staff_delivery import (
    WeChatDeliveryAccepted,
    WeChatDeliveryClaim,
    WeChatDeliveryClaimResult,
    WeChatDeliveryList,
    WeChatDeliveryReceipt,
    WeChatDeliveryRequest,
    WeChatDeliveryState,
)
from app.services import wechat_staff_delivery as service
from app.services.chat_service import validate_platform_and_project
from app.services.im_access import authenticate_im_actor
from app.services.wechat_local_bridge import authorize_bridge_visitor
from app.utils.encoding import parse_visitor_channel_id

router = APIRouter()
bearer = HTTPBearer(auto_error=False)


async def authorize(
    db: Session,
    body: WeChatDeliveryRequest,
    credentials: HTTPAuthorizationCredentials | None,
    *,
    check_scope: bool = True,
) -> tuple[Platform, Visitor]:
    platform, _ = validate_platform_and_project(body.platform_api_key, db)
    if platform.type != "wechat_local":
        raise HTTPException(403, "该接口仅用于原个人微信桥接")
    try:
        visitor_id = parse_visitor_channel_id(body.channel_id)
    except ValueError as error:
        raise HTTPException(403, "会话标识无效") from error
    visitor = db.scalar(
        select(Visitor).where(
            Visitor.id == visitor_id,
            Visitor.project_id == platform.project_id,
            Visitor.platform_id == platform.id,
            Visitor.deleted_at.is_(None),
        )
    )
    if visitor is None:
        raise HTTPException(403, "会话不属于当前渠道")
    if check_scope:
        actor, _ = await authorize_bridge_visitor(
            db,
            platform,
            body.channel_id,
            body.channel_type,
            credentials,
        )
    else:
        # Revocation stops new sends, but cannot erase an already performed send.

        if credentials is None:
            raise HTTPException(401, "缺少访客回执凭据")
        proof_actor = authenticate_im_actor(
            db, body.channel_id, credentials.credentials
        )
        if proof_actor.id != visitor.id:
            raise HTTPException(403, "个人微信访客身份不一致")
        actor = visitor
    if actor.id != visitor.id:
        raise HTTPException(403, "个人微信访客身份不一致")
    return platform, visitor


@router.post("/ready", response_model=WeChatDeliveryList)
async def ready(
    body: WeChatDeliveryRequest,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> WeChatDeliveryList:
    platform, visitor = await authorize(db, body, credentials)
    return WeChatDeliveryList(replies=service.ready(db, platform.id, visitor))


@router.post("/{delivery_id}/check", response_model=WeChatDeliveryState)
async def check(
    delivery_id: UUID,
    body: WeChatDeliveryRequest,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> WeChatDeliveryState:
    platform, visitor = await authorize(db, body, credentials)
    row = service.check(db, delivery_id, platform.id, visitor, body.attempt_id)
    return WeChatDeliveryState(state=row.state)


@router.post("/{delivery_id}/claim", response_model=WeChatDeliveryClaimResult)
async def claim(
    delivery_id: UUID,
    body: WeChatDeliveryClaim,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> WeChatDeliveryClaimResult:
    platform, visitor = await authorize(db, body, credentials)
    return WeChatDeliveryClaimResult(
        claim_id=service.claim(
            db,
            delivery_id,
            platform.id,
            visitor,
            body.attempt_id,
        )
    )


@router.post("/{delivery_id}/receipt", response_model=WeChatDeliveryAccepted)
async def receipt(
    delivery_id: UUID,
    body: WeChatDeliveryReceipt,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> WeChatDeliveryAccepted:
    platform, visitor = await authorize(
        db, body, credentials, check_scope=False
    )
    await service.confirm(
        db,
        delivery_id,
        platform.id,
        visitor,
        body.claim_id,
        body.result,
        body.receipt,
    )
    return WeChatDeliveryAccepted()
