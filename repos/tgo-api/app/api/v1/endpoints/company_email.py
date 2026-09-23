"""Public email flows share rate limits and never disclose account existence."""

from hashlib import sha256

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models import Staff
from app.models.company_account import CompanyAccount
from app.schemas.company_email import (
    ActionRequest,
    ActionResponse,
    EmailCodeRequest,
    EmailRequest,
    PasswordResetRequest,
)
from app.services.company_email import (
    complete_password_reset,
    complete_verification,
    complete_verification_code,
    enqueue_action,
    require_mail_configuration,
)
from app.services.registration_limit import limit_registration

router = APIRouter()


async def limit_action(request: Request) -> None:
    if not settings.SAAS_ENABLED:
        raise HTTPException(404, "企业邮箱服务尚未启用")
    peer = request.client.host if request.client else "unknown"
    await limit_registration(f"email-actions:{peer}")


@router.post(
    "/verify-email",
    response_model=ActionResponse,
    dependencies=[Depends(limit_action)],
)
def verify_email(
    payload: ActionRequest, db: Session = Depends(get_db)
) -> ActionResponse:
    complete_verification(db, payload.token)
    db.commit()
    return ActionResponse(message="邮箱验证成功，请登录并输入试用激活码")


@router.post(
    "/verify-email-code",
    response_model=ActionResponse,
    dependencies=[Depends(limit_action)],
)
async def verify_email_code(
    payload: EmailCodeRequest, db: Session = Depends(get_db)
) -> ActionResponse:
    normalized = str(payload.email).strip().lower()
    await limit_registration(
        f"verify-code:{sha256(normalized.encode()).hexdigest()}"
    )
    complete_verification_code(db, normalized, payload.code)
    db.commit()
    return ActionResponse(message="邮箱验证成功，请登录并输入试用激活码")


@router.post(
    "/reset-password",
    response_model=ActionResponse,
    dependencies=[Depends(limit_action)],
)
def reset_password(
    payload: PasswordResetRequest, db: Session = Depends(get_db)
) -> ActionResponse:
    complete_password_reset(db, payload.token, payload.password)
    db.commit()
    return ActionResponse(message="密码已重置，请使用新密码登录")


async def request_mail(db: Session, email: str, reset: bool) -> ActionResponse:
    require_mail_configuration()
    normalized = email.strip().lower()
    await limit_registration(
        f"email-recipient:{sha256(normalized.encode()).hexdigest()}"
    )
    staff = db.scalar(
        select(Staff).where(
            func.lower(Staff.username) == normalized,
            Staff.deleted_at.is_(None),
        )
    )
    account = (
        db.get(CompanyAccount, staff.project_id)
        if staff is not None and not reset
        else None
    )
    if staff is not None and (
        (reset and staff.email_verified_at is not None)
        or (
            not reset
            and staff.email_verified_at is None
            and not staff.account_enabled
            and account is not None
            and account.status == "pending"
            and not account.trial_granted
        )
    ):
        enqueue_action(db, staff, "reset" if reset else "verify")
        db.commit()
    return ActionResponse(message="若该邮箱符合条件，邮件将发送，请检查收件箱和垃圾邮件")


@router.post(
    "/forgot-password",
    response_model=ActionResponse,
    status_code=202,
    dependencies=[Depends(limit_action)],
)
async def forgot_password(
    payload: EmailRequest, db: Session = Depends(get_db)
) -> ActionResponse:
    return await request_mail(db, str(payload.email), True)


@router.post(
    "/resend-verification",
    response_model=ActionResponse,
    status_code=202,
    dependencies=[Depends(limit_action)],
)
async def resend_verification(
    payload: EmailRequest, db: Session = Depends(get_db)
) -> ActionResponse:
    return await request_mail(db, str(payload.email), False)
