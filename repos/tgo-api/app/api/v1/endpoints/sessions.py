"""Session management endpoints."""

from typing import Literal, Optional, cast
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.logging import get_logger
from app.core.security import require_permission
from app.models import Staff, AssignmentSource, SessionCommandDelivery
from app.schemas.visitor_session import (
    SessionHandoffSummary,
    VisitorSessionResponse,
)
from app.services.session_service import close_visitor_session
from app.services.staff_session_access import begin_staff_session_change
from app.services.session_command_delivery import (
    deliver_session_command,
    prepare_operation_id,
)
from app.services.session_command_membership import deliver_channel_effects
from app.services.transfer_service import transfer_to_staff
from app.api.current_access_route import CurrentAccessRoute
from app.services.profile_access import OperationGuard
from app.services.profile_access import not_found
from app.services.staff_record_access import (
    require_staff_record_access,
    staff_record_guard,
)
from app.services.session_handoff_summary import load_handoff_summary

logger = get_logger("api.sessions")
router = APIRouter(route_class=CurrentAccessRoute)
original_bearer = HTTPBearer(auto_error=False)


@router.get(
    "/visitor/{visitor_id}/handoff-summary",
    response_model=SessionHandoffSummary,
    summary="查看当前会话的交接摘录",
)
async def get_handoff_summary(
    visitor_id: UUID,
    http_request: Request,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("visitors:read")),
    credentials: HTTPAuthorizationCredentials
    | None = Depends(original_bearer),
) -> SessionHandoffSummary:
    access = require_staff_record_access(
        db, credentials, current_user, "visitors:read"
    )
    summary, guard = await load_handoff_summary(db, access, visitor_id)
    http_request.state.operation_guard = guard
    return summary


async def finish_session_notice(
    db: Session, response: Response, operation_id: UUID, guard: OperationGuard
) -> None:
    guard.check()
    try:
        effects_status = await deliver_channel_effects(db, operation_id)
        if effects_status == "missing":
            effects_status = "unknown"
    except Exception as exc:
        db.rollback()
        logger.error(
            "Session command channel effect status unavailable",
            extra={
                "operation_id": str(operation_id),
                "error_type": type(exc).__name__,
            },
        )
        effects_status = "unknown"
    guard.check()
    try:
        notice_status = await deliver_session_command(db, operation_id)
        if notice_status == "missing":
            notice_status = "unknown"
    except Exception as exc:
        db.rollback()
        logger.error(
            "Session command notice status unavailable",
            extra={
                "operation_id": str(operation_id),
                "error_type": type(exc).__name__,
            },
        )
        notice_status = "unknown"
    guard.check()
    response.headers["X-Session-Operation-ID"] = str(operation_id)
    response.headers["X-Session-Notification-Status"] = notice_status
    response.headers["X-Session-Effects-Status"] = effects_status


# ============================================================================
# Request/Response schemas
# ============================================================================


class TransferSessionRequest(BaseModel):
    """Request schema for session transfer."""

    target_staff_id: UUID = Field(
        ..., description="ID of the staff to transfer the session to"
    )
    reason: Optional[str] = Field(
        None, max_length=500, description="Reason for transfer"
    )


class TransferSessionResponse(BaseModel):
    """Response schema for session transfer."""

    success: bool = Field(
        ..., description="Whether the transfer was successful"
    )
    message: str = Field(..., description="Status message")
    old_session_id: UUID = Field(..., description="ID of the closed session")
    new_session_id: Optional[UUID] = Field(
        None, description="ID of the new session"
    )
    visitor_id: UUID = Field(..., description="Visitor ID")
    from_staff_id: UUID = Field(..., description="Original staff ID")
    to_staff_id: UUID = Field(..., description="Target staff ID")


class SessionCommandReceiptResponse(BaseModel):
    operation_id: UUID
    session_id: UUID
    visitor_id: UUID
    to_staff_id: UUID | None
    kind: Literal["close", "transfer"]
    notification_status: Literal[
        "pending", "sending", "sent", "unknown", "skipped"
    ]
    attempts: int
    effects_status: Literal["pending", "done", "unknown", "skipped"]
    effects_attempts: int


@router.get(
    "/operations/{operation_id}",
    response_model=SessionCommandReceiptResponse,
    summary="查询原会话操作的通知回执",
)
def get_session_command_receipt(
    operation_id: UUID,
    http_request: Request,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("visitors:update")),
    credentials: HTTPAuthorizationCredentials
    | None = Depends(original_bearer),
) -> SessionCommandReceiptResponse:
    access = require_staff_record_access(
        db, credentials, current_user, "visitors:update"
    )
    row = (
        db.query(SessionCommandDelivery)
        .filter(
            SessionCommandDelivery.id == operation_id,
            SessionCommandDelivery.project_id == access.project_id,
        )
        .first()
    )
    if row is None or (
        row.actor_staff_id != access.staff_id and current_user.role != "admin"
    ):
        raise not_found()
    guard = staff_record_guard(access, (), "visitors:update")
    guard.check()
    http_request.state.operation_guard = guard
    return SessionCommandReceiptResponse(
        operation_id=row.id,
        session_id=row.session_id,
        visitor_id=row.visitor_id,
        to_staff_id=row.to_staff_id,
        kind=cast(Literal["close", "transfer"], row.kind),
        notification_status=cast(
            Literal["pending", "sending", "sent", "unknown", "skipped"],
            row.status,
        ),
        attempts=row.attempts,
        effects_status=cast(
            Literal["pending", "done", "unknown", "skipped"],
            row.effects_status,
        ),
        effects_attempts=row.effects_attempts,
    )


@router.post(
    "/{session_id}/close",
    response_model=VisitorSessionResponse,
    summary="通过会话ID关闭会话",
    description="关闭指定的会话，更新会话状态和最后消息信息，并发送系统通知。",
)
async def close_session_by_id(
    session_id: UUID,
    http_request: Request,
    http_response: Response,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("visitors:update")),
    credentials: HTTPAuthorizationCredentials
    | None = Depends(original_bearer),
    requested_operation_id: UUID
    | None = Header(None, alias="X-Session-Operation-ID"),
) -> VisitorSessionResponse:
    """
    通过会话ID关闭会话。
    
    只有会话负责人或管理员可以关闭会话。
    """
    operation_id = prepare_operation_id(db, requested_operation_id)
    session, scope = begin_staff_session_change(
        db,
        credentials,
        current_user,
        session_id=session_id,
    )

    try:
        session = await close_visitor_session(
            db=db,
            session=session,
            closed_by_staff=current_user,
            send_notification=True,
            command_scope=scope,
            operation_id=operation_id,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        )

    guard = scope.completed_guard(
        session, status="closed", staff_id=scope.original_staff_id
    )
    guard.check()
    http_request.state.operation_guard = guard
    await finish_session_notice(db, http_response, operation_id, guard)
    return VisitorSessionResponse.model_validate(session)


@router.post(
    "/visitor/{visitor_id}/close",
    response_model=VisitorSessionResponse,
    summary="通过访客ID关闭会话",
    description="关闭指定访客当前进行中的会话。如果访客有多个进行中的会话，关闭最新的一个。",
)
async def close_session_by_visitor_id(
    visitor_id: UUID,
    http_request: Request,
    http_response: Response,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("visitors:update")),
    credentials: HTTPAuthorizationCredentials
    | None = Depends(original_bearer),
    requested_operation_id: UUID
    | None = Header(None, alias="X-Session-Operation-ID"),
) -> VisitorSessionResponse:
    """
    通过访客ID关闭会话。
    
    关闭指定访客当前进行中（status=open）的会话。
    如果访客有多个进行中的会话，关闭最新创建的一个。
    只有会话负责人或管理员可以关闭会话。
    """
    operation_id = prepare_operation_id(db, requested_operation_id)
    session, scope = begin_staff_session_change(
        db,
        credentials,
        current_user,
        visitor_id=visitor_id,
    )

    try:
        session = await close_visitor_session(
            db=db,
            session=session,
            closed_by_staff=current_user,
            send_notification=True,
            command_scope=scope,
            operation_id=operation_id,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        )

    guard = scope.completed_guard(
        session, status="closed", staff_id=scope.original_staff_id
    )
    guard.check()
    http_request.state.operation_guard = guard
    await finish_session_notice(db, http_response, operation_id, guard)
    return VisitorSessionResponse.model_validate(session)


# ============================================================================
# 转接和接入接口
# ============================================================================


@router.post(
    "/{session_id}/transfer",
    response_model=TransferSessionResponse,
    summary="转接会话",
    description="将当前会话转接给另一个客服。",
)
async def transfer_session(
    session_id: UUID,
    request: TransferSessionRequest,
    http_request: Request,
    http_response: Response,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("visitors:update")),
    credentials: HTTPAuthorizationCredentials
    | None = Depends(original_bearer),
    requested_operation_id: UUID
    | None = Header(None, alias="X-Session-Operation-ID"),
) -> TransferSessionResponse:
    """
    转接会话给另一个客服。
    
    - 将当前会话的客服更换为目标客服
    - 保持会话不关闭
    - 发送通知
    
    只有会话负责人或管理员可以转接会话。
    """
    operation_id = prepare_operation_id(db, requested_operation_id)
    session, scope = begin_staff_session_change(
        db,
        credentials,
        current_user,
        session_id=session_id,
    )

    # Check target staff exists and is available
    target_staff = (
        db.query(Staff)
        .filter(
            Staff.id == request.target_staff_id,
            Staff.project_id == current_user.project_id,
            Staff.deleted_at.is_(None),
        )
        .first()
    )

    if not target_staff:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target staff not found",
        )

    if target_staff.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot transfer to yourself",
        )

    if not target_staff.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Target staff is not active",
        )

    if target_staff.service_paused:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Target staff has paused service",
        )

    session_id = session.id
    visitor_id = session.visitor_id
    from_staff_id = session.staff_id or current_user.id

    # Transfer to target staff (reuse existing session, don't close, don't send notification)
    notes = f"Transferred from staff {current_user.id}"
    if request.reason:
        notes += f": {request.reason}"

    result = await transfer_to_staff(
        db=db,
        visitor_id=visitor_id,
        project_id=current_user.project_id,
        source=AssignmentSource.TRANSFER,
        assigned_by_staff_id=current_user.id,
        target_staff_id=request.target_staff_id,
        session_id=session_id,
        session_scope=scope,
        operation_id=operation_id,
        platform_id=session.platform_id,
        notes=notes,
        skip_queue_status_check=True,
        auto_commit=True,
        send_notification=False,
    )

    if not result.success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to transfer session: {result.message}",
        )

    if result.session is None or result.session.id != session_id:
        raise HTTPException(status_code=409, detail="Original session changed")
    guard = scope.completed_guard(
        result.session, status="open", staff_id=request.target_staff_id
    )
    guard.check()
    http_request.state.operation_guard = guard

    await finish_session_notice(db, http_response, operation_id, guard)

    logger.info(
        f"Session {session_id} transferred from staff {from_staff_id} to {request.target_staff_id}",
        extra={
            "session_id": str(session_id),
            "visitor_id": str(visitor_id),
            "from_staff_id": str(from_staff_id),
            "to_staff_id": str(request.target_staff_id),
            "reason": request.reason,
        },
    )

    return TransferSessionResponse(
        success=True,
        message="Session transferred successfully",
        old_session_id=session_id,
        new_session_id=result.session.id if result.session else session_id,
        visitor_id=visitor_id,
        from_staff_id=from_staff_id,
        to_staff_id=request.target_staff_id,
    )


@router.post(
    "/visitor/{visitor_id}/transfer",
    response_model=TransferSessionResponse,
    summary="通过访客ID转接会话",
    description="将指定访客的当前会话转接给另一个客服。",
)
async def transfer_session_by_visitor_id(
    visitor_id: UUID,
    request: TransferSessionRequest,
    http_request: Request,
    http_response: Response,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("visitors:update")),
    credentials: HTTPAuthorizationCredentials
    | None = Depends(original_bearer),
    requested_operation_id: UUID
    | None = Header(None, alias="X-Session-Operation-ID"),
) -> TransferSessionResponse:
    """
    通过访客ID转接会话。
    
    - 查找访客当前进行中的会话
    - 将当前会话的客服更换为目标客服
    - 保持会话不关闭
    
    只有会话负责人或管理员可以转接会话。
    """
    operation_id = prepare_operation_id(db, requested_operation_id)
    session, scope = begin_staff_session_change(
        db,
        credentials,
        current_user,
        visitor_id=visitor_id,
    )

    # Check target staff exists and is available
    target_staff = (
        db.query(Staff)
        .filter(
            Staff.id == request.target_staff_id,
            Staff.project_id == current_user.project_id,
            Staff.deleted_at.is_(None),
        )
        .first()
    )

    if not target_staff:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target staff not found",
        )

    if target_staff.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot transfer to yourself",
        )

    if not target_staff.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Target staff is not active",
        )

    if target_staff.service_paused:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Target staff has paused service",
        )

    session_id = session.id
    from_staff_id = session.staff_id or current_user.id

    # Transfer to target staff (reuse existing session, don't close, don't send notification)
    notes = f"Transferred from staff {current_user.id}"
    if request.reason:
        notes += f": {request.reason}"

    result = await transfer_to_staff(
        db=db,
        visitor_id=visitor_id,
        project_id=current_user.project_id,
        source=AssignmentSource.TRANSFER,
        assigned_by_staff_id=current_user.id,
        target_staff_id=request.target_staff_id,
        session_id=session_id,
        session_scope=scope,
        operation_id=operation_id,
        platform_id=session.platform_id,
        notes=notes,
        skip_queue_status_check=True,
        auto_commit=True,
        send_notification=False,
    )

    if not result.success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to transfer session: {result.message}",
        )

    if result.session is None or result.session.id != session_id:
        raise HTTPException(status_code=409, detail="Original session changed")
    guard = scope.completed_guard(
        result.session, status="open", staff_id=request.target_staff_id
    )
    guard.check()
    http_request.state.operation_guard = guard

    await finish_session_notice(db, http_response, operation_id, guard)

    logger.info(
        f"Session transferred via visitor_id {visitor_id} from staff {from_staff_id} to {request.target_staff_id}",
        extra={
            "session_id": str(session_id),
            "visitor_id": str(visitor_id),
            "from_staff_id": str(from_staff_id),
            "to_staff_id": str(request.target_staff_id),
            "reason": request.reason,
        },
    )

    return TransferSessionResponse(
        success=True,
        message="Session transferred successfully",
        old_session_id=session_id,
        new_session_id=result.session.id if result.session else session_id,
        visitor_id=visitor_id,
        from_staff_id=from_staff_id,
        to_staff_id=request.target_staff_id,
    )
