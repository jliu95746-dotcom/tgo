"""Visitor Waiting Queue endpoints."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.logging import get_logger
from app.core.security import require_permission
from app.models import (
    Staff,
    Visitor,
    VisitorWaitingQueue,
    WaitingStatus,
    AssignmentSource,
)
from app.schemas.visitor_waiting_queue import (
    WaitingQueueListResponse,
    WaitingQueueDetailResponse,
    WaitingQueueListParams,
    AcceptVisitorRequest,
    AcceptVisitorResponse,
    VisitorBriefResponse,
    StaffBriefResponse,
    WaitingStatusEnum,
    QueueSourceEnum,
    QueueUrgencyEnum,
)
from app.services.transfer_service import transfer_to_staff
from app.services.queue_lifecycle import cancel_entry
from app.utils.encoding import build_visitor_channel_id
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE

logger = get_logger("api.visitor_waiting_queue")

router = APIRouter()


def _build_visitor_brief(visitor: Visitor) -> VisitorBriefResponse:
    """Build visitor brief response."""
    return VisitorBriefResponse(
        id=visitor.id,
        name=visitor.name,
        nickname=visitor.nickname,
        avatar_url=visitor.avatar_url,
        platform_open_id=visitor.platform_open_id,
    )


def _build_staff_brief(staff: Staff) -> StaffBriefResponse:
    """Build staff brief response."""
    return StaffBriefResponse(
        id=staff.id,
        name=staff.name,
        username=staff.username,
        avatar_url=staff.avatar_url,
    )


def _build_queue_detail_response(entry: VisitorWaitingQueue) -> WaitingQueueDetailResponse:
    """Build detailed queue response from model."""
    visitor_brief = None
    staff_brief = None
    
    if entry.visitor:
        visitor_brief = _build_visitor_brief(entry.visitor)
    if entry.assigned_staff:
        staff_brief = _build_staff_brief(entry.assigned_staff)
    
    return WaitingQueueDetailResponse(
        id=entry.id,
        project_id=entry.project_id,
        visitor_id=entry.visitor_id,
        session_id=entry.session_id,
        assigned_staff_id=entry.assigned_staff_id,
        source=entry.source,
        urgency=entry.urgency,
        position=entry.position,
        priority=entry.priority,
        status=entry.status,
        visitor_message=entry.visitor_message,
        reason=entry.reason,
        channel_id=entry.channel_id,
        channel_type=entry.channel_type,
        retry_count=None,
        wait_duration_seconds=entry.wait_duration_seconds,
        entered_at=entry.entered_at,
        assigned_at=entry.assigned_at,
        exited_at=entry.exited_at,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
        visitor=visitor_brief,
        assigned_staff=staff_brief,
        extra_metadata=entry.extra_metadata,
    )


@router.get(
    "",
    response_model=WaitingQueueListResponse,
    summary="获取等待队列列表",
    description="获取当前项目的访客等待队列列表，支持分页和筛选。",
)
async def list_waiting_queue(
    status: Optional[WaitingStatusEnum] = Query(
        None, description="按状态筛选"
    ),
    source: Optional[QueueSourceEnum] = Query(
        None, description="按来源筛选"
    ),
    urgency: Optional[QueueUrgencyEnum] = Query(
        None, description="按紧急程度筛选"
    ),
    visitor_id: Optional[UUID] = Query(
        None, description="按访客ID筛选"
    ),
    limit: int = Query(20, ge=1, le=100, description="每页数量"),
    offset: int = Query(0, ge=0, description="跳过数量"),
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("visitors:read")),
) -> WaitingQueueListResponse:
    """
    获取等待队列列表。
    
    - 默认按优先级降序、位置升序排列（高优先级、先入队的排前面）
    - 支持按状态、来源、紧急程度、访客ID筛选
    - 返回访客和客服的简要信息
    """
    # Build query
    query = db.query(VisitorWaitingQueue).filter(
        VisitorWaitingQueue.project_id == current_user.project_id,
    )
    
    # Apply filters
    if status:
        query = query.filter(VisitorWaitingQueue.status == status.value)
    if source:
        query = query.filter(VisitorWaitingQueue.source == source.value)
    if urgency:
        query = query.filter(VisitorWaitingQueue.urgency == urgency.value)
    if visitor_id:
        query = query.filter(VisitorWaitingQueue.visitor_id == visitor_id)
    
    # Get total count
    total = query.count()
    
    # Apply ordering and pagination
    entries = (
        query
        .options(
            joinedload(VisitorWaitingQueue.visitor),
            joinedload(VisitorWaitingQueue.assigned_staff),
        )
        .order_by(
            VisitorWaitingQueue.priority.desc(),
            VisitorWaitingQueue.position.asc(),
        )
        .offset(offset)
        .limit(limit)
        .all()
    )
    
    # Build response
    items = [_build_queue_detail_response(entry) for entry in entries]
    
    return WaitingQueueListResponse(
        items=items,
        pagination={
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_next": offset + limit < total,
            "has_prev": offset > 0,
        },
    )


@router.get(
    "/waiting",
    response_model=WaitingQueueListResponse,
    summary="获取等待中的队列",
    description="快捷接口：只返回状态为 waiting 的队列条目。",
)
async def list_waiting_only(
    urgency: Optional[QueueUrgencyEnum] = Query(
        None, description="按紧急程度筛选"
    ),
    limit: int = Query(20, ge=1, le=100, description="每页数量"),
    offset: int = Query(0, ge=0, description="跳过数量"),
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("visitors:read")),
) -> WaitingQueueListResponse:
    """
    获取等待中的队列（只返回 waiting 状态）。
    
    这是一个快捷接口，专门用于客服查看待接入的访客列表。
    """
    query = db.query(VisitorWaitingQueue).filter(
        VisitorWaitingQueue.project_id == current_user.project_id,
        VisitorWaitingQueue.status == WaitingStatus.WAITING.value,
    )
    
    if urgency:
        query = query.filter(VisitorWaitingQueue.urgency == urgency.value)
    
    total = query.count()
    
    entries = (
        query
        .options(
            joinedload(VisitorWaitingQueue.visitor),
        )
        .order_by(
            VisitorWaitingQueue.priority.desc(),
            VisitorWaitingQueue.position.asc(),
        )
        .offset(offset)
        .limit(limit)
        .all()
    )
    
    items = [_build_queue_detail_response(entry) for entry in entries]
    
    return WaitingQueueListResponse(
        items=items,
        pagination={
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_next": offset + limit < total,
            "has_prev": offset > 0,
        },
    )


@router.get(
    "/count",
    summary="获取等待队列数量",
    description="获取当前项目等待中的访客数量。",
)
async def get_waiting_count(
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("visitors:read")),
) -> dict:
    """
    获取等待队列数量统计。
    
    返回各状态的数量统计。
    """
    project_id = current_user.project_id
    
    waiting_count = db.query(VisitorWaitingQueue).filter(
        VisitorWaitingQueue.project_id == project_id,
        VisitorWaitingQueue.status == WaitingStatus.WAITING.value,
    ).count()
    
    assigned_count = db.query(VisitorWaitingQueue).filter(
        VisitorWaitingQueue.project_id == project_id,
        VisitorWaitingQueue.status == WaitingStatus.ASSIGNED.value,
    ).count()
    
    return {
        "waiting": waiting_count,
        "assigned": assigned_count,
        "total": waiting_count + assigned_count,
    }


@router.get(
    "/{entry_id}",
    response_model=WaitingQueueDetailResponse,
    summary="获取队列条目详情",
    description="根据ID获取单个队列条目的详细信息。",
)
async def get_queue_entry(
    entry_id: UUID,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("visitors:read")),
) -> WaitingQueueDetailResponse:
    """获取单个队列条目详情。"""
    entry = (
        db.query(VisitorWaitingQueue)
        .options(
            joinedload(VisitorWaitingQueue.visitor),
            joinedload(VisitorWaitingQueue.assigned_staff),
        )
        .filter(
            VisitorWaitingQueue.id == entry_id,
            VisitorWaitingQueue.project_id == current_user.project_id,
        )
        .first()
    )
    
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Queue entry not found",
        )
    
    return _build_queue_detail_response(entry)


@router.post(
    "/accept",
    response_model=AcceptVisitorResponse,
    summary="接入访客",
    description="客服接入等待队列中的访客。（改用 /visitors/{visitor_id}/accept 接口）",
)
async def accept_visitor(
    request: AcceptVisitorRequest,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("visitors:update")),
) -> AcceptVisitorResponse:
    """
    客服接入访客。
    
    - 从等待队列中接入指定的访客
    - 调用 transfer_to_staff 完成分配
    - 更新队列状态为 assigned
    """
    # Find the queue entry by visitor_id (get the latest waiting entry)
    entry = (
        db.query(VisitorWaitingQueue)
        .options(joinedload(VisitorWaitingQueue.visitor))
        .filter(
            VisitorWaitingQueue.visitor_id == request.visitor_id,
            VisitorWaitingQueue.project_id == current_user.project_id,
            VisitorWaitingQueue.status == WaitingStatus.WAITING.value,
        )
        .order_by(VisitorWaitingQueue.entered_at.desc())
        .first()
    )
    
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No waiting queue entry found for this visitor",
        )
    
    # Calculate wait duration before transfer
    wait_duration = entry.wait_duration_seconds
    visitor = entry.visitor
    
    # Call transfer_to_staff service with target_staff_id
    result = await transfer_to_staff(
        db=db,
        visitor_id=entry.visitor_id,
        project_id=entry.project_id,
        source=AssignmentSource.MANUAL,
        visitor_message=entry.visitor_message,
        assigned_by_staff_id=current_user.id,
        target_staff_id=current_user.id,  # Direct assignment to current staff
        session_id=entry.session_id,
        platform_id=visitor.platform_id if visitor else None,
        ai_disabled=entry.ai_disabled,
        add_to_queue_if_no_staff=False,
        expected_queue_entry_id=entry.id,
        notes=f"Accepted from waiting queue. {request.notes or ''}".strip(),
    )
    
    if not result.success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.message,
        )
    
    # Update queue entry status to assigned
    # Queue transition was committed atomically by transfer_to_staff.
    
    # Determine channel_id
    channel_id = entry.channel_id
    if not channel_id and visitor:
        channel_id = build_visitor_channel_id(visitor.id)
    
    channel_type = entry.channel_type or CHANNEL_TYPE_CUSTOMER_SERVICE
    
    logger.info(
        f"Staff {current_user.id} accepted visitor {request.visitor_id} from queue",
        extra={
            "staff_id": str(current_user.id),
            "visitor_id": str(request.visitor_id),
            "entry_id": str(entry.id),
            "wait_duration_seconds": wait_duration,
        },
    )
    
    return AcceptVisitorResponse(
        success=True,
        message="访客已成功接入",
        entry_id=entry.id,
        visitor_id=entry.visitor_id,
        staff_id=current_user.id,
        session_id=result.session.id if result.session else None,
        channel_id=channel_id,
        channel_type=channel_type,
        wait_duration_seconds=wait_duration,
    )


@router.post(
    "/{entry_id}/cancel",
    summary="取消队列条目",
    description="取消等待队列中的条目（访客离开或其他原因）。",
)
async def cancel_queue_entry(
    entry_id: UUID,
    reason: Optional[str] = Query(None, max_length=255, description="取消原因"),
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("visitors:update")),
) -> dict:
    """
    取消队列条目。
    
    用于访客离开或其他需要取消等待的情况。
    """
    entry = db.query(VisitorWaitingQueue).filter(
        VisitorWaitingQueue.id == entry_id,
        VisitorWaitingQueue.project_id == current_user.project_id,
    ).first()
    
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Queue entry not found",
        )
    
    if entry.status != WaitingStatus.WAITING.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Queue entry is not in waiting status (current: {entry.status})",
        )
    
    # Update status
    if not cancel_entry(db, entry.id, current_user.project_id, reason):
        raise HTTPException(409, "Queue entry is no longer waiting")
    
    db.commit()
    
    logger.info(
        f"Queue entry {entry_id} cancelled",
        extra={
            "entry_id": str(entry_id),
            "cancelled_by": str(current_user.id),
            "reason": reason,
        },
    )
    
    return {
        "success": True,
        "message": "队列条目已取消",
        "entry_id": str(entry_id),
    }
