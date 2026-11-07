"Channel delivery fences reuse existing platform and scoped visitor \
identity."

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import Platform, Visitor
from app.models.customer_turn import CustomerTurn, CustomerTurnPart
from app.schemas.chat import ChatCompletionRequest
from app.schemas.customer_turn import (
    TurnClaim,
    TurnDeliveryReceipt,
    TurnDeliveryRequest,
    TurnPollRequest,
    TurnReply,
    TurnReplyList,
    TurnState,
    TurnSuperseded,
)
from app.services.customer_turn_context import (
    TurnExecution,
    current_execution,
    ensure_current,
)
from app.services.customer_turn_store import TurnStore
from app.services.chat_service import validate_platform_and_project
from app.services.wechat_local_bridge import authorize_bridge_visitor
from app.utils.encoding import parse_visitor_channel_id

router = APIRouter()
bearer = HTTPBearer(auto_error=False)


async def authorize(
    db: Session,
    body: TurnPollRequest | TurnDeliveryRequest,
    credentials: HTTPAuthorizationCredentials | None,
    *,
    check_scope: bool = True,
) -> tuple[Platform, Visitor]:
    platform, _ = validate_platform_and_project(body.platform_api_key, db)
    try:
        visitor_id = parse_visitor_channel_id(body.channel_id)
    except ValueError as error:
        raise HTTPException(403, "会话标识无效") from error
    visitor = db.scalar(
        select(Visitor).where(
            Visitor.id == visitor_id,
            Visitor.platform_id == platform.id,
            Visitor.project_id == platform.project_id,
            Visitor.deleted_at.is_(None),
        )
    )
    if visitor is None:
        raise HTTPException(403, "会话不属于当前渠道")
    if platform.type == "wechat_local":
        if check_scope:
            actor, _ = await authorize_bridge_visitor(
                db, platform, body.channel_id, body.channel_type, credentials
            )
        else:
            from app.services.im_access import authenticate_im_actor

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


async def checked_turn(
    db: Session,
    turn_id: UUID,
    body: TurnDeliveryRequest,
    credentials: HTTPAuthorizationCredentials | None,
) -> CustomerTurn:
    platform, visitor = await authorize(db, body, credentials)
    turn = TurnStore(db).get(turn_id)
    if (
        turn.project_id != platform.project_id
        or turn.platform_id != platform.id
        or turn.visitor_id != visitor.id
        or not turn.external_delivery
    ):
        raise HTTPException(403, "回复不属于当前渠道会话")
    token = current_execution.set(
        TurnExecution(
            turn.id,
            body.version,
            turn.project_id,
            turn.platform_id,
            turn.visitor_id,
            True,
        )
    )
    try:
        await ensure_current()
    except TurnSuperseded as error:
        raise HTTPException(409, "本轮回复或接待权限已变化，停止发送") from error
    finally:
        current_execution.reset(token)
    return turn


@router.post("/inbound", response_model=None)
async def inbound(
    body: ChatCompletionRequest,
    request: Request,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> StreamingResponse | dict[str, object]:
    from app.api.v1.endpoints.chat import chat_completion

    body.wukongim_only, body.stream = True, False
    return await chat_completion(
        req=body,
        request=request,
        db=db,
        credentials=credentials,
        x_saas_service_token=request.headers.get("x-saas-service-token"),
    )


@router.post("/media-ready", response_model=None)
async def media_ready(
    body: ChatCompletionRequest,
    request: Request,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> StreamingResponse | dict[str, object]:
    platform, _ = validate_platform_and_project(body.api_key, db)
    part = db.scalar(
        select(CustomerTurnPart).where(
            CustomerTurnPart.platform_id == platform.id,
            CustomerTurnPart.source_message_id == body.source_message_id,
        )
    )
    if part is None or body.media_pending or body.msg_type not in (2, 4):
        raise HTTPException(409, "未登记原始媒体消息，或媒体仍未就绪")
    return await inbound(
        body=body, request=request, db=db, credentials=credentials
    )


@router.post("/ready", response_model=TurnReplyList)
async def ready(
    body: TurnPollRequest,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> TurnReplyList:
    platform, visitor = await authorize(db, body, credentials)
    rows = db.scalars(
        select(CustomerTurn)
        .where(
            CustomerTurn.platform_id == platform.id,
            CustomerTurn.visitor_id == visitor.id,
            CustomerTurn.external_delivery.is_(True),
            CustomerTurn.state == "ready",
        )
        .order_by(CustomerTurn.created_at)
        .limit(20)
    )
    return TurnReplyList(
        replies=[
            TurnReply(
                turn_id=row.id,
                version=row.version,
                client_msg_no=row.client_msg_no,
                text=row.reply_text or "",
                ready_at=row.ready_at,
            )
            for row in rows
        ]
    )


@router.post("/{turn_id}/check", response_model=TurnState)
async def check(
    turn_id: UUID,
    body: TurnDeliveryRequest,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> TurnState:
    turn = await checked_turn(db, turn_id, body, credentials)
    if turn.state != "ready":
        raise HTTPException(409, "回复未就绪或已进入发送流程")
    return TurnState(turn_id=turn.id, version=turn.version, state=turn.state)


@router.post("/{turn_id}/claim", response_model=TurnClaim)
async def claim(
    turn_id: UUID,
    body: TurnDeliveryRequest,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> TurnClaim:
    await checked_turn(db, turn_id, body, credentials)
    turn = TurnStore(db).claim(turn_id, body.version)
    db.commit()
    visitor = db.get(Visitor, turn.visitor_id)
    if visitor is None or turn.claim_id is None:
        raise HTTPException(409, "回复所属访客已变化")
    return TurnClaim(
        turn_id=turn.id,
        version=turn.version,
        claim_id=turn.claim_id,
        text=turn.reply_text or "",
        source_message_id=TurnStore(db).parts(turn.id)[-1].source_message_id,
        customer_uid=visitor.platform_open_id,
    )


@router.post("/{turn_id}/receipt", response_model=TurnState)
async def receipt(
    turn_id: UUID,
    body: TurnDeliveryReceipt,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> TurnState:
    platform, visitor = await authorize(
        db, body, credentials, check_scope=False
    )
    turn = TurnStore(db).get(turn_id, lock=True)
    if (
        turn.platform_id != platform.id
        or turn.visitor_id != visitor.id
        or not turn.external_delivery
    ):
        raise HTTPException(403, "发送回执不属于当前渠道")
    # Revoked reception can report an already observed receipt, but cannot
    # claim
    # another send. Acknowledging a receipt never performs an external send.

    turn = TurnStore(db).confirm(
        turn.id, body.version, body.claim_id, body.result, body.receipt
    )
    db.commit()
    return TurnState(turn_id=turn.id, version=turn.version, state=turn.state)
