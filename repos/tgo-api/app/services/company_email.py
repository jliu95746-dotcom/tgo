"""One-time email verification and password recovery; caller owns commit."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from hmac import new as hmac_new
from secrets import randbelow, token_urlsafe
from typing import Literal
from urllib.parse import urlsplit

from fastapi import HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import get_password_hash
from app.models import Project, Staff
from app.models.company_account import (
    CompanyAccount,
    EmailAction,
    EmailOutbox,
)
from app.utils.crypto import encrypt_str


class MailPayload(BaseModel):
    recipient: EmailStr
    subject: str
    body: str


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def require_mail_configuration() -> None:
    target = urlsplit(settings.SAAS_WEB_BASE_URL)
    local = target.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        not target.netloc
        or target.username
        or target.password
        or target.query
        or target.fragment
        or (target.scheme != "https" and not (local and target.scheme == "http"))
        or not settings.SAAS_SMTP_HOST
        or not settings.SAAS_SMTP_FROM
    ):
        raise HTTPException(503, "企业验证邮件服务尚未配置")


def verification_code_hash(staff_id: object, code: str) -> str:
    message = f"verify-code:{staff_id}:{code}".encode("utf-8")
    return hmac_new(settings.SECRET_KEY.encode("utf-8"), message, sha256).hexdigest()


def enqueue_action(
    db: Session,
    staff: Staff,
    purpose: Literal["verify", "reset", "invite"],
) -> EmailAction:
    require_mail_configuration()
    now = datetime.now(timezone.utc)
    code = f"{randbelow(1_000_000):06d}" if purpose == "verify" else None
    token = token_urlsafe(32)
    action = EmailAction(
        project_id=staff.project_id,
        staff_id=staff.id,
        purpose=purpose,
        token_hash=(
            verification_code_hash(staff.id, code)
            if code is not None
            else sha256(token.encode()).hexdigest()
        ),
        expires_at=now + timedelta(
            minutes=15 if code is not None else 30 if purpose == "reset" else 1440
        ),
    )
    db.add(action)
    db.flush()
    route = "reset-password" if purpose == "reset" else "verify-email"
    subject = "域见密码重置" if purpose == "reset" else "域见企业邮箱验证"
    if purpose == "invite":
        route, subject = "accept-invitation", "域见企业成员邀请"
    if code is not None:
        body = (
            f"您的企业注册验证码是：{code}\n\n"
            "验证码 15 分钟内有效，请勿告知他人。\n"
            "如非本人申请，请忽略此邮件。"
        )
    else:
        link = f"{settings.SAAS_WEB_BASE_URL.rstrip('/')}/auth/{route}#token={token}"
        body = f"请打开以下链接完成操作：\n{link}\n\n如非本人申请，请忽略此邮件。"
    payload = MailPayload(
        recipient=staff.username,
        subject=subject,
        body=body,
    )
    db.add(
        EmailOutbox(
            project_id=staff.project_id,
            action_id=action.id,
            encrypted_payload=encrypt_str(payload.model_dump_json()),
            available_at=now,
        )
    )
    return action


def lock_action(db: Session, token: str, purpose: str) -> EmailAction:
    return lock_action_digest(db, sha256(token.encode()).hexdigest(), purpose)


def lock_action_digest(db: Session, digest: str, purpose: str) -> EmailAction:
    # All account changes lock the company first to serialize grants and members.
    action = db.scalar(
        select(EmailAction).where(
            EmailAction.token_hash == digest,
            EmailAction.purpose == purpose,
        )
    )
    if action is None:
        raise HTTPException(400, "验证链接无效或已过期")
    project = db.scalar(
        select(Project)
        .where(
            Project.id == action.project_id,
            Project.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if project is None:
        raise HTTPException(400, "验证链接无效或已过期")
    db.refresh(action, with_for_update=True)
    if action.used_at is None and utc(action.expires_at) <= datetime.now(timezone.utc):
        raise HTTPException(400, "验证链接无效或已过期")
    return action


def complete_verification(db: Session, token: str) -> None:
    action = lock_action(db, token, "verify")
    if action.used_at is not None:
        return
    complete_email_action(db, action)


def complete_verification_code(db: Session, email: str, code: str) -> None:
    staff = db.scalar(
        select(Staff).where(
            Staff.username == email.strip().lower(),
            Staff.deleted_at.is_(None),
        )
    )
    if staff is None:
        raise HTTPException(400, "验证码无效或已过期")
    digest = verification_code_hash(staff.id, code)
    try:
        action = lock_action_digest(db, digest, "verify")
    except HTTPException as exc:
        raise HTTPException(400, "验证码无效或已过期") from exc
    if action.staff_id != staff.id or action.used_at is not None:
        raise HTTPException(400, "验证码无效或已过期")
    complete_email_action(db, action)


def complete_email_action(db: Session, action: EmailAction) -> None:
    staff = db.get(Staff, action.staff_id)
    account = db.get(CompanyAccount, action.project_id)
    if (
        staff is None
        or staff.deleted_at is not None
        or staff.project_id != action.project_id
        or account is None
        or account.status != "pending"
        or account.trial_granted
    ):
        raise HTTPException(409, "企业状态已变化，请联系管理员")
    now = datetime.now(timezone.utc)
    staff.account_enabled = True
    staff.email_verified_at = now
    action.used_at = now


def complete_password_reset(db: Session, token: str, password: str) -> None:
    action = lock_action(db, token, "reset")
    if action.used_at is not None:
        raise HTTPException(400, "验证链接已使用，请重新申请")
    staff = db.get(Staff, action.staff_id)
    if (
        staff is None
        or staff.deleted_at is not None
        or staff.project_id != action.project_id
        or staff.email_verified_at is None
    ):
        raise HTTPException(400, "验证链接无效或已过期")
    staff.password_hash = get_password_hash(password)
    staff.token_version += 1
    # Invalidate every outstanding recovery link; reset never enables an account.
    for pending in db.scalars(
        select(EmailAction).where(
            EmailAction.staff_id == staff.id,
            EmailAction.purpose == "reset",
            EmailAction.used_at.is_(None),
        )
    ):
        pending.used_at = datetime.now(timezone.utc)
