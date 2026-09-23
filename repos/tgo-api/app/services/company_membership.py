"""Company-row locks serialize seat reservations and administrator changes."""

from datetime import datetime, timezone
from secrets import token_urlsafe
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import get_password_hash
from app.models import Project, Staff
from app.models.company_account import CompanyAccount, EmailAction
from app.models.company_invitation import CompanyInvitation
from app.services.company_email import enqueue_action, lock_action, utc


def lock_company(
    db: Session, project_id: UUID, actor: Staff | None = None
) -> CompanyAccount | None:
    project = db.scalar(
        select(Project)
        .where(
            Project.id == project_id,
            Project.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if project is None:
        raise HTTPException(404, "企业不存在")
    if actor is not None:
        db.refresh(actor)
        if (
            actor.project_id != project_id
            or actor.role != "admin"
            or not actor.account_enabled
            or actor.deleted_at is not None
        ):
            raise HTTPException(403, "仅企业管理员可以管理成员")
    account = db.get(CompanyAccount, project_id)
    if account is not None:
        db.refresh(account)
    return account


def seat_usage(db: Session, project_id: UUID) -> tuple[int, int]:
    used = (
        db.scalar(
            select(func.count())
            .select_from(Staff)
            .where(
                Staff.project_id == project_id,
                Staff.role != "agent",
                Staff.account_enabled.is_(True),
                Staff.deleted_at.is_(None),
            )
        )
        or 0
    )
    reserved = (
        db.scalar(
            select(func.count())
            .select_from(CompanyInvitation)
            .where(
                CompanyInvitation.project_id == project_id,
                CompanyInvitation.status == "pending",
                CompanyInvitation.expires_at > datetime.now(timezone.utc),
            )
        )
        or 0
    )
    return used, reserved


def ensure_seat(
    db: Session, account: CompanyAccount, *, consume_reservation: bool = False
) -> None:
    if (
        account.status not in {"trial", "active"}
        or account.expires_at is None
        or utc(account.expires_at) <= datetime.now(timezone.utc)
    ):
        raise HTTPException(402, "套餐已过期或尚未启用")
    used, reserved = seat_usage(db, account.project_id)
    if used + reserved + (0 if consume_reservation else 1) > account.seat_limit:
        raise HTTPException(409, "人工席位不足，请先加购或停用其他账号")


def validate_human_role_change(target: Staff, role: str) -> None:
    if (target.role == "agent") != (role == "agent"):
        raise HTTPException(409, "人工账号与 AI 账号不能相互转换，请通过成员邀请或 AI 员工管理创建")


def protect_last_admin(db: Session, target: Staff) -> None:
    if (
        target.role != "admin"
        or not target.account_enabled
        or target.deleted_at is not None
    ):
        return
    others = (
        db.scalar(
            select(func.count())
            .select_from(Staff)
            .where(
                Staff.project_id == target.project_id,
                Staff.id != target.id,
                Staff.role == "admin",
                Staff.account_enabled.is_(True),
                Staff.deleted_at.is_(None),
            )
        )
        or 0
    )
    if not others:
        raise HTTPException(409, "必须保留至少一个启用的管理员")


def invite_member(
    db: Session, actor: Staff, email: str, role: Literal["admin", "user"]
) -> CompanyInvitation:
    account = lock_company(db, actor.project_id, actor)
    if account is None:
        raise HTTPException(409, "请先为企业配置套餐")
    ensure_seat(db, account)
    normalized = email.strip().lower()
    existing = db.scalar(select(Staff).where(func.lower(Staff.username) == normalized))
    if existing is not None:
        pending = db.scalar(
            select(CompanyInvitation.id).where(
                CompanyInvitation.staff_id == existing.id,
                CompanyInvitation.status == "pending",
                CompanyInvitation.expires_at > datetime.now(timezone.utc),
            )
        )
        if (
            existing.project_id != actor.project_id
            or existing.account_enabled
            or existing.email_verified_at is not None
            or pending is not None
        ):
            raise HTTPException(409, "该邮箱已被使用或已存在有效邀请")
        target = existing
        target.deleted_at = None
        target.role = role
    else:
        target = Staff(
            project_id=actor.project_id,
            username=normalized,
            nickname=normalized.split("@")[0],
            role=role,
            password_hash=get_password_hash(token_urlsafe(32)),
            account_enabled=False,
            status="offline",
        )
        db.add(target)
        db.flush()
    action = enqueue_action(db, target, "invite")
    invitation = CompanyInvitation(
        project_id=actor.project_id,
        staff_id=target.id,
        action_id=action.id,
        email=normalized,
        role=role,
        expires_at=action.expires_at,
    )
    db.add(invitation)
    return invitation


def revoke_invitation(db: Session, actor: Staff, identifier: UUID) -> None:
    lock_company(db, actor.project_id, actor)
    invitation = db.scalar(
        select(CompanyInvitation).where(
            CompanyInvitation.id == identifier,
            CompanyInvitation.project_id == actor.project_id,
        )
    )
    if invitation is None:
        raise HTTPException(404, "邀请不存在")
    if invitation.status == "accepted":
        raise HTTPException(409, "邀请已接受，请通过成员管理停用账号")
    invitation.status = "revoked"
    action = db.get(EmailAction, invitation.action_id)
    if action is not None:
        action.used_at = datetime.now(timezone.utc)


def accept_invitation(db: Session, token: str, password: str) -> None:
    action = lock_action(db, token, "invite")
    invitation = db.scalar(
        select(CompanyInvitation)
        .where(
            CompanyInvitation.action_id == action.id,
        )
        .execution_options(populate_existing=True)
    )
    if (
        invitation is None
        or invitation.status != "pending"
        or action.used_at is not None
    ):
        raise HTTPException(400, "邀请已使用、撤销或过期")
    account = db.get(CompanyAccount, action.project_id)
    if account is None:
        raise HTTPException(409, "企业套餐不存在")
    db.refresh(account)
    ensure_seat(db, account, consume_reservation=True)
    target = db.get(Staff, invitation.staff_id)
    if (
        target is None
        or target.project_id != action.project_id
        or target.account_enabled
        or target.deleted_at is not None
    ):
        raise HTTPException(409, "账号状态已变化")
    target.password_hash = get_password_hash(password)
    target.email_verified_at = datetime.now(timezone.utc)
    target.account_enabled = True
    target.role = invitation.role
    target.token_version += 1
    invitation.status = "accepted"
    action.used_at = datetime.now(timezone.utc)
