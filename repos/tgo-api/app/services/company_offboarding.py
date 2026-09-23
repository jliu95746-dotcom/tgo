"""Move open work before disabling a company member, within one transaction."""

from datetime import datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AssignmentSource,
    ChannelMember,
    Staff,
    Visitor,
    VisitorAssignmentRule,
    VisitorSession,
)
from app.schemas.company_membership import MemberChange
from app.services.company_membership import (
    ensure_seat,
    lock_company,
    protect_last_admin,
)
from app.services.transfer_service import (
    _add_to_waiting_queue,
    transfer_to_staff,
)
from app.utils.encoding import build_visitor_channel_id


async def change_member(
    db: Session, actor: Staff, target_id: UUID, change: MemberChange
) -> Staff:
    account = lock_company(db, actor.project_id, actor)
    target = db.scalar(
        select(Staff)
        .where(
            Staff.id == target_id,
            Staff.project_id == actor.project_id,
            Staff.deleted_at.is_(None),
            Staff.role != "agent",
        )
        .execution_options(populate_existing=True)
    )
    if target is None:
        raise HTTPException(404, "成员不存在")
    disabling = change.account_enabled is False and target.account_enabled
    if disabling or (change.role is not None and change.role != "admin"):
        protect_last_admin(db, target)
    if change.account_enabled is True and not target.account_enabled:
        if target.email_verified_at is None:
            raise HTTPException(409, "请先让成员通过邮箱接受邀请")
        if account is not None:
            ensure_seat(db, account)
    if disabling:
        await move_open_work(db, actor, target, change)
        target.token_version += 1
        target.status = "offline"
    if change.account_enabled is not None:
        target.account_enabled = change.account_enabled
    if change.role is not None:
        target.role = change.role
    return target


async def move_open_work(
    db: Session, actor: Staff, target: Staff, change: MemberChange
) -> None:
    sessions = list(
        db.scalars(
            select(VisitorSession)
            .where(
                VisitorSession.project_id == actor.project_id,
                VisitorSession.staff_id == target.id,
                VisitorSession.status == "open",
            )
            .order_by(VisitorSession.visitor_id, VisitorSession.id)
        )
    )
    if not sessions:
        return
    if bool(change.transfer_to) == change.return_to_queue:
        raise HTTPException(409, "该成员有未结束会话，请选择接收客服或退回公共队列")
    if change.transfer_to:
        receiver = db.get(Staff, change.transfer_to)
        if (
            receiver is None
            or receiver.id == target.id
            or receiver.project_id != actor.project_id
            or receiver.role == "agent"
            or not receiver.is_available_for_service
        ):
            raise HTTPException(409, "接收客服不可用")
    rule = db.scalar(
        select(VisitorAssignmentRule).where(
            VisitorAssignmentRule.project_id == actor.project_id,
        )
    )
    for session in sessions:
        visitor = db.scalar(
            select(Visitor)
            .where(
                Visitor.id == session.visitor_id,
                Visitor.project_id == actor.project_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if visitor is None:
            raise HTTPException(409, "会话关联访客不存在")
        if change.transfer_to:
            result = await transfer_to_staff(
                db=db,
                visitor_id=visitor.id,
                project_id=actor.project_id,
                source=AssignmentSource.TRANSFER,
                target_staff_id=change.transfer_to,
                assigned_by_staff_id=actor.id,
                session_id=session.id,
                auto_commit=False,
                send_notification=False,
            )
            if not result.success:
                raise HTTPException(409, "会话转交失败，账号保持启用")
        else:
            session.staff_id = None
            visitor.set_status_queued()
            await _add_to_waiting_queue(
                db,
                actor.project_id,
                visitor.id,
                visitor,
                session.id,
                None,
                "客服账号停用，退回公共队列",
                rule,
                visitor.ai_disabled,
            )
        channel = build_visitor_channel_id(visitor.id)
        for membership in db.scalars(
            select(ChannelMember).where(
                ChannelMember.project_id == actor.project_id,
                ChannelMember.channel_id == channel,
                ChannelMember.member_id == target.id,
                ChannelMember.member_type == "staff",
                ChannelMember.deleted_at.is_(None),
            )
        ):
            membership.deleted_at = datetime.utcnow()
