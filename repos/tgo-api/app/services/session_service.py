"""Session service for managing visitor sessions."""

from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import Staff, VisitorSession, SessionStatus, VisitorServiceStatus, ChannelMember
from app.services.wukongim_client import wukongim_client
from app.services.queue_trigger_service import trigger_queue_for_staff
from app.services.staff_session_access import StaffSessionScope
from app.services.session_command_delivery import record_close
from app.utils.encoding import build_visitor_channel_id
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE

logger = get_logger("services.session")


async def close_visitor_session(
    db: Session,
    session: VisitorSession,
    closed_by_staff: Optional[Staff] = None,
    send_notification: bool = True,
    auto_commit: bool = True,
    reason: Optional[str] = None,
    command_scope: StaffSessionScope | None = None,
    operation_id: UUID | None = None,
) -> VisitorSession:
    """
    关闭访客会话的公用方法。
    
    Args:
        db: 数据库会话
        session: 要关闭的会话对象（需要已加载 visitor 关系）
        closed_by_staff: 关闭会话的客服（可选，用于发送通知）
        send_notification: 是否发送系统通知消息
        auto_commit: 是否自动提交事务
        reason: 关闭原因（可选，用于日志）
        
    Returns:
        关闭后的会话对象
        
    Raises:
        ValueError: 如果会话已经关闭
    """
    # Check if session is already closed
    if session.status == SessionStatus.CLOSED.value:
        raise ValueError("Session is already closed")
    
    close_reason = reason or ("by staff" if closed_by_staff else "unknown")
    
    logger.info(
        f"Closing session {session.id}",
        extra={
            "session_id": str(session.id),
            "visitor_id": str(session.visitor_id),
            "closed_by": str(closed_by_staff.id) if closed_by_staff else None,
            "reason": close_reason,
        }
    )
    
    if command_scope is not None:
        if not auto_commit:
            raise ValueError("Scoped session close requires a committed transaction")
        command_scope.check(db)

    # 1. Get channel last message from WuKongIM
    channel_id = build_visitor_channel_id(session.visitor_id)
    last_message = None
    try:
        last_message = await wukongim_client.get_channel_last_message(
            channel_id=channel_id,
            channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
        )
        
    except Exception as e:
        logger.warning(f"Failed to get channel last message: {e}")
        # Continue even if we can't get last message
    
    if command_scope is not None:
        session = command_scope.check(db, lock=True)
    if last_message:
        session.last_message_seq = last_message.message_seq
        if last_message.timestamp:
            session.last_message_at = datetime.fromtimestamp(last_message.timestamp)

    # 2. Close the session
    session.close()  # This sets status=closed, closed_at, and calculates duration
    session.updated_at = datetime.utcnow()
    
    # 3. Update visitor service status
    visitor = session.visitor
    if visitor:
        visitor.service_status = VisitorServiceStatus.CLOSED.value
        visitor.updated_at = datetime.utcnow()
    if command_scope is not None and send_notification:
        if closed_by_staff is None:
            raise ValueError("Scoped session close requires a staff actor")
        record_close(
            db, command_scope, session, closed_by_staff,
            operation_id or uuid4(),
        )
    
    if auto_commit:
        db.commit()
        db.refresh(session)
    else:
        db.flush()
    
    operation_guard = command_scope.completed_guard(
        session, status="closed", staff_id=command_scope.original_staff_id,
    ) if command_scope is not None else None
    if operation_guard is not None:
        operation_guard.check()

    logger.info(
        f"Session {session.id} closed successfully",
        extra={
            "session_id": str(session.id),
            "duration_seconds": session.duration_seconds,
            "closed_at": str(session.closed_at),
            "reason": close_reason,
        }
    )
    
    # 4. Remove staff from WuKongIM channel and ChannelMember table
    if session.staff_id and command_scope is None:
        if operation_guard is not None:
            operation_guard.check()
        # Remove from ChannelMember table (soft delete)
        channel_member = db.query(ChannelMember).filter(
            ChannelMember.channel_id == channel_id,
            ChannelMember.member_id == session.staff_id,
            ChannelMember.member_type == "staff",
            ChannelMember.deleted_at.is_(None),
        ).first()
        
        if channel_member:
            channel_member.deleted_at = datetime.utcnow()
            db.flush()
            if auto_commit:
                db.commit()
            logger.info(f"Removed staff {session.staff_id} from ChannelMember table for channel {channel_id}")
        
        # Remove from WuKongIM channel (async, non-blocking)
        try:
            if operation_guard is not None:
                operation_guard.check()
            staff_uid = f"{session.staff_id}-staff"
            await wukongim_client.remove_channel_subscribers(
                channel_id=channel_id,
                channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
                subscribers=[staff_uid],
            )
            logger.info(f"Removed staff {session.staff_id} from WuKongIM channel {channel_id}")
        except Exception as e:
            logger.warning(f"Failed to remove staff from WuKongIM channel: {e}")
            # Don't fail if removal fails - it's not critical
    
    # 5. Send session closed system message (async, non-blocking)
    if send_notification and command_scope is None:
        if operation_guard is not None:
            operation_guard.check()
        try:
            notice_staff_uid: str | None = None
            staff_name = None
            
            if closed_by_staff:
                notice_staff_uid = f"{closed_by_staff.id}-staff"
                staff_name = closed_by_staff.name or closed_by_staff.nickname or closed_by_staff.username
            
            await wukongim_client.send_session_closed_message(
                from_uid=notice_staff_uid or "system",
                channel_id=channel_id,
                channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
                staff_uid=notice_staff_uid,
                staff_name=staff_name,
            )
            logger.info(f"Sent session closed message for session {session.id}")
        except Exception as e:
            logger.error(f"Failed to send session closed message: {e}")
            # Don't fail if notification fails
    
    # 6. Delete the conversation from staff's WuKongIM (async, non-blocking)
    if session.staff_id and command_scope is None:
        if operation_guard is not None:
            operation_guard.check()
        try:
            staff_uid = f"{session.staff_id}-staff"
            await wukongim_client.delete_conversation(
                uid=staff_uid,
                channel_id=channel_id,
                channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
            )
            logger.info(f"Deleted conversation for staff {session.staff_id}, session {session.id}")
        except Exception as e:
            logger.warning(f"Failed to delete conversation from WuKongIM: {e}")
            # Don't fail if deletion fails - it's not critical
    
    # 7. Trigger queue processing - staff has freed up a slot
    if session.staff_id and session.project_id:
        if operation_guard is not None:
            operation_guard.check()
        try:
            await trigger_queue_for_staff(session.staff_id, session.project_id)
        except Exception as e:
            logger.error(f"Failed to trigger queue processing: {e}")
            # Don't fail if trigger fails
    
    return session
