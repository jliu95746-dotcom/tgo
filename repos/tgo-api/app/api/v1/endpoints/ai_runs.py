"""Authenticated cancellation of an owned customer or staff reply."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.database import get_db
from app.core.security import get_current_active_user, require_admin
from app.models import Platform, Staff
from app.models.ai_execution_attempt import AIExecutionAttempt
from app.models.ai_interaction_run import AIInteractionRun
from app.models.platform import DIRECT_VISITOR_PLATFORM_TYPES
from app.schemas.business_query_audit import BusinessQueryAuditList
from app.schemas.ai_runs import (
    CancelByClientNoRequest,
    ReplyCancelResponse,
    StaffCancelRequest,
    AIExecutionAttemptLookup,
    AIExecutionAttemptSummary,
    AIExecutionToolEventSummary,
    AIExecutionBusinessQuerySummary,
    AIReplyOriginRequest,
    AIReplyOriginEvidence,
    VisitorAIReplyOriginRequest,
)
from app.services.ai_interaction_run_service import (
    list_ai_business_queries, list_ai_tool_events,
)
from app.services.ai_reply_origin import verify_ai_reply_origins
from app.services.business_query_audit_client import read_business_message_audits
from app.services.channel_access import require_staff_channel_access
from app.services.reply_cancellation import cancel_reply, get_reply_run
from app.services.visitor_data_access import (
    require_visitor_data_access,
    authorize_visitor_data_channel,
    reauthorize_visitor_data,
)
from app.services.visitor_reply_access import require_visitor_reply_access
from app.services.workflow_trace import find_ai_request_for_channel_message
from app.services.wukongim_client import wukongim_client
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE

router = APIRouter()


@router.post(
    "/reply-origins/verify",
    response_model=list[AIReplyOriginEvidence],
    summary="核验当前客户会话中 AI 回复的真实来源",
)
async def verify_reply_origins_for_staff(
    request: AIReplyOriginRequest,
    response: Response,
    current_user: Staff = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> list[AIReplyOriginEvidence]:
    await require_staff_channel_access(
        db, current_user, request.channel_id, CHANNEL_TYPE_CUSTOMER_SERVICE,
    )
    response.headers["Cache-Control"] = "no-store"
    return await verify_ai_reply_origins(
        db, current_user.project_id, request.channel_id,
        request.client_msg_nos, wukongim_client,
    )


@router.post(
    "/reply-origins/verify-by-visitor",
    response_model=list[AIReplyOriginEvidence],
    summary="核验当前访客会话中 AI 回复的真实来源",
)
async def verify_reply_origins_for_visitor(
    request: VisitorAIReplyOriginRequest,
    response: Response,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(
        HTTPBearer(auto_error=False)
    ),
) -> list[AIReplyOriginEvidence]:
    platform = db.query(Platform).filter(
        Platform.api_key == request.platform_api_key,
        Platform.type.in_(DIRECT_VISITOR_PLATFORM_TYPES),
        Platform.is_active.is_(True),
        Platform.deleted_at.is_(None),
    ).first()
    if platform is None:
        raise HTTPException(401, "Invalid platform API key")
    access = require_visitor_data_access(db, platform, credentials)
    authorize_visitor_data_channel(
        access, request.channel_id, CHANNEL_TYPE_CUSTOMER_SERVICE,
    )
    evidence = await verify_ai_reply_origins(
        db, access.project_id, request.channel_id,
        request.client_msg_nos, wukongim_client,
    )
    reauthorize_visitor_data(db, access)
    response.headers["Cache-Control"] = "no-store"
    return evidence


@router.post(
    "/attempts/lookup-channel-message",
    response_model=list[AIExecutionAttemptSummary],
    summary="按客户原消息查看 AI 执行记录",
)
def lookup_ai_attempts_for_message(
    request: AIExecutionAttemptLookup,
    limit: int = Query(20, ge=1, le=100),
    skip: int = Query(0, ge=0),
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> list[AIExecutionAttemptSummary]:
    run = find_ai_request_for_channel_message(
        db, current_user.project_id, request.channel_id,
        request.source_message_id,
    )
    if run is None:
        return []
    attempts = db.scalars(select(AIExecutionAttempt).where(
        AIExecutionAttempt.project_id == current_user.project_id,
        AIExecutionAttempt.interaction_run_id == run.id,
    ).order_by(
        AIExecutionAttempt.started_at.desc(),
        AIExecutionAttempt.execution_id.desc(),
    ).limit(limit).offset(skip)).all()
    return [AIExecutionAttemptSummary.model_validate(row) for row in attempts]


@router.get(
    "/attempts/{execution_id}/tool-events",
    response_model=list[AIExecutionToolEventSummary],
    summary="查看 AI 单次执行的工具事件",
)
def get_ai_tool_events(
    execution_id: UUID,
    limit: int = Query(50, ge=1, le=100),
    skip: int = Query(0, ge=0),
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> list[AIExecutionToolEventSummary]:
    events = list_ai_tool_events(
        db, current_user.project_id, execution_id, limit, skip,
    )
    return [AIExecutionToolEventSummary.model_validate(row) for row in events]


@router.get(
    "/attempts/{execution_id}/business-queries",
    response_model=list[AIExecutionBusinessQuerySummary],
    summary="查看 AI 单次执行关联的脱敏业务查询编号",
)
def get_ai_business_queries(
    execution_id: UUID,
    limit: int = Query(50, ge=1, le=100),
    skip: int = Query(0, ge=0),
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> list[AIExecutionBusinessQuerySummary]:
    records = list_ai_business_queries(
        db, current_user.project_id, execution_id, limit, skip,
    )
    return [AIExecutionBusinessQuerySummary.model_validate(row) for row in records]


@router.get(
    "/attempts/{execution_id}/business-audit-observations",
    response_model=BusinessQueryAuditList,
    summary="查看 Plugin 对本次 AI 回复观察到的脱敏业务查询尝试",
)
async def get_ai_business_audit_observations(
    execution_id: UUID,
    response: Response,
    limit: int = Query(20, ge=1, le=20),
    skip: int = Query(0, ge=0),
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> BusinessQueryAuditList:
    match = db.execute(
        select(AIExecutionAttempt, AIInteractionRun)
        .join(AIInteractionRun, AIExecutionAttempt.interaction_run_id == AIInteractionRun.id)
        .where(
            AIExecutionAttempt.execution_id == execution_id,
            AIExecutionAttempt.project_id == current_user.project_id,
            AIInteractionRun.project_id == current_user.project_id,
        )
    ).one_or_none()
    if match is None:
        raise HTTPException(404, "AI execution attempt not found")
    attempt, run = match
    response.headers["Cache-Control"] = "no-store"
    return await read_business_message_audits(
        current_user.project_id, run.source_message_id,
        attempt.response_client_msg_no, skip, limit,
    )


@router.post(
    "/cancel-by-client",
    status_code=202,
    response_model=ReplyCancelResponse,
    summary="停止当前平台的客户回复",
)
async def cancel_by_client_no(
    req: CancelByClientNoRequest,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(
        HTTPBearer(auto_error=False)
    ),
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
    access = require_visitor_data_access(db, platform, credentials)
    item = await get_reply_run(str(platform.project_id), req.client_msg_no)
    require_visitor_reply_access(db, access, item, req.client_msg_no)
    response = await cancel_reply(item)
    reauthorize_visitor_data(db, access)
    return response


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
        await require_staff_channel_access(
            db, current_user, item.channel_id, item.channel_type
        )
    return await cancel_reply(item)
