"""Public signup, separate from authenticated staff management."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.config import settings
from app.schemas.registration import RegistrationRequest
from app.schemas.company_email import ActionResponse, EmailRequest
from app.schemas.staff import StaffResponse
from app.services.project_registration import register_project_account
from app.services.registration_limit import limit_registration
from app.services.registration_email import (
    consume_registration_code,
    send_registration_code,
)

router = APIRouter()


@router.post(
    "/registration-code", response_model=ActionResponse, status_code=202
)
async def request_registration_code(
    payload: EmailRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> ActionResponse:
    if not settings.PUBLIC_REGISTRATION_ENABLED or not settings.SAAS_ENABLED:
        raise HTTPException(403, "Public registration is disabled")
    await limit_registration(
        request.client.host if request.client else "unknown"
    )
    await send_registration_code(db, str(payload.email))
    return ActionResponse(message="若该邮箱可以注册，验证码将发送，请检查收件箱和垃圾邮件")


@router.post("/register", response_model=StaffResponse, status_code=201)
async def register_staff(
    payload: RegistrationRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> StaffResponse:
    if not settings.PUBLIC_REGISTRATION_ENABLED:
        raise HTTPException(403, "Public registration is disabled")
    # Use the trusted server peer, never a user-supplied forwarding header.
    await limit_registration(
        request.client.host if request.client else "unknown"
    )
    if settings.SAAS_ENABLED:
        if payload.verification_code is None:
            raise HTTPException(422, "请输入邮箱验证码")
        await consume_registration_code(
            str(payload.username), payload.verification_code
        )
    return await register_project_account(
        db, payload, email_verified=settings.SAAS_ENABLED
    )
