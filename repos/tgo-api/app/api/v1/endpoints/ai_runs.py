"""Authenticated cancellation of an owned customer or staff reply."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_active_user
from app.models import Platform, Staff, Visitor
from app.schemas.ai_runs import (
    CancelByClientNoRequest,
    ReplyCancelResponse,
    StaffCancelRequest,
)
from app.services.reply_cancellation import cancel_reply, get_reply_run
from app.utils.encoding import parse_visitor_channel_id

router = APIRouter()


@router.post(
    "/cancel-by-client",
    status_code=202,
    response_model=ReplyCancelResponse,
    summary="停止当前平台的客户回复",
)
async def cancel_by_client_no(
    req: CancelByClientNoRequest,
    db: Session = Depends(get_db),
) -> ReplyCancelResponse:
    platform = (
        db.query(Platform)
        .filter(
            Platform.api_key == req.platform_api_key,
            Platform.is_active.is_(True),
            Platform.deleted_at.is_(None),
        )
        .first()
    )
    if platform is None:
        raise HTTPException(401, "Invalid platform API key")
    item = await get_reply_run(str(platform.project_id), req.client_msg_no)
    if item.channel_type != 251:
        raise HTTPException(404, "Reply not found")
    try:
        visitor_id = parse_visitor_channel_id(item.channel_id)
    except ValueError as exc:
        raise HTTPException(404, "Reply not found") from exc
    visitor = (
        db.query(Visitor)
        .filter(
            Visitor.id == visitor_id,
            Visitor.project_id == platform.project_id,
            Visitor.platform_id == platform.id,
            Visitor.deleted_at.is_(None),
        )
        .first()
    )
    if visitor is None:
        raise HTTPException(404, "Reply not found")
    return await cancel_reply(item)


@router.post(
    "/cancel",
    status_code=202,
    response_model=ReplyCancelResponse,
    summary="停止当前项目的 AI 回复",
)
async def cancel_run_by_staff(
    req: StaffCancelRequest,
    current_user: Staff = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> ReplyCancelResponse:
    item = await get_reply_run(str(current_user.project_id), req.client_msg_no)
    from app.services.staff_conversation_scope import restricted_staff
    if restricted_staff(current_user):
        from app.services.channel_access import require_staff_channel_access
        if item.channel_type != 251:
            raise HTTPException(403, "仅可停止本人负责的客户会话回复")
        await require_staff_channel_access(db, current_user, item.channel_id, item.channel_type)
    return await cancel_reply(item)
