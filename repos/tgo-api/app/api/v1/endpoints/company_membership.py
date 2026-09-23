"""Administrator-only company management and purpose-bound invitation acceptance."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.endpoints.company_email import limit_action
from app.core.config import settings
from app.core.database import get_db
from app.core.security import require_admin
from app.models import Staff
from app.models.company_account import CompanyAccount
from app.models.company_invitation import CompanyInvitation
from app.schemas.company_email import ActionResponse, PasswordResetRequest
from app.schemas.company_membership import (
    CompanyFeatureStatus,
    InvitationCreate,
    InvitationResponse,
    MemberChange,
    SeatResponse,
)
from app.schemas.staff import StaffResponse
from app.services.company_membership import (
    accept_invitation,
    invite_member,
    revoke_invitation,
    seat_usage,
)
from app.services.company_offboarding import change_member


def require_company_features() -> None:
    if not settings.SAAS_ENABLED or not settings.SAAS_BILLING_ENABLED:
        raise HTTPException(404, "企业订阅服务尚未启用")


router = APIRouter(dependencies=[Depends(require_company_features)])
public_router = APIRouter()


@public_router.get("/status", response_model=CompanyFeatureStatus)
def company_status() -> CompanyFeatureStatus:
    return CompanyFeatureStatus(
        enabled=settings.SAAS_ENABLED and settings.SAAS_BILLING_ENABLED
    )


@router.get("/seats", response_model=SeatResponse)
def seats(
    db: Session = Depends(get_db), actor: Staff = Depends(require_admin())
) -> SeatResponse:
    used, reserved = seat_usage(db, actor.project_id)
    account = db.get(CompanyAccount, actor.project_id)
    return SeatResponse(
        used=used,
        reserved=reserved,
        limit=account.seat_limit if account else None,
    )


@router.get("/invitations", response_model=list[InvitationResponse])
def invitations(
    db: Session = Depends(get_db), actor: Staff = Depends(require_admin())
) -> list[CompanyInvitation]:
    return list(
        db.scalars(
            select(CompanyInvitation)
            .where(
                CompanyInvitation.project_id == actor.project_id,
            )
            .order_by(CompanyInvitation.created_at.desc())
            .limit(100)
        )
    )


@router.post("/invitations", response_model=InvitationResponse, status_code=201)
def invite(
    payload: InvitationCreate,
    db: Session = Depends(get_db),
    actor: Staff = Depends(require_admin()),
) -> CompanyInvitation:
    if len(str(payload.email)) > 50:
        raise HTTPException(422, "邮箱最长 50 个字符")
    try:
        invitation = invite_member(db, actor, str(payload.email), payload.role)
        db.commit()
        db.refresh(invitation)
        return invitation
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "邮箱已被使用或邀请状态已变化") from exc


@router.delete("/invitations/{identifier}", status_code=204)
def revoke(
    identifier: UUID,
    db: Session = Depends(get_db),
    actor: Staff = Depends(require_admin()),
) -> None:
    revoke_invitation(db, actor, identifier)
    db.commit()


@router.post(
    "/invitations/accept",
    response_model=ActionResponse,
    dependencies=[Depends(limit_action)],
)
def accept(
    payload: PasswordResetRequest, db: Session = Depends(get_db)
) -> ActionResponse:
    accept_invitation(db, payload.token, payload.password)
    db.commit()
    return ActionResponse(message="邀请已接受，请登录企业工作台")


@router.patch("/members/{identifier}", response_model=StaffResponse)
async def member_change(
    identifier: UUID,
    payload: MemberChange,
    db: Session = Depends(get_db),
    actor: Staff = Depends(require_admin()),
) -> StaffResponse:
    target = await change_member(db, actor, identifier, payload)
    db.commit()
    return StaffResponse.model_validate(target)
