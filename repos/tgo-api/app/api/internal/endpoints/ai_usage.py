"""Database authorization for trusted AI workers, isolated from public routes."""

from datetime import datetime, timezone
from secrets import compare_digest
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.ai_usage import AIUsageReservation
from app.models.company_account import CompanyAccount
from app.schemas.ai_usage import UsageAuthorization, UsageAuthorizationResult
from app.services.ai_usage import require_service
from app.services.company_email import utc
from app.schemas.company_resources import CompanyResources
from app.services.company_resources import resources
from app.services.platform_models import runtime_model


def require_quota_service(x_saas_service_token: str | None = Header(None)) -> None:
    if (
        settings.SAAS_INTERNAL_TOKEN is None
        or not x_saas_service_token
        or not compare_digest(
            x_saas_service_token, settings.SAAS_INTERNAL_TOKEN.get_secret_value()
        )
    ):
        raise HTTPException(403, "内部服务身份无效")


router = APIRouter(dependencies=[Depends(require_quota_service)])


@router.get("/resources/{project_id}", response_model=CompanyResources)
def resource_limits(
    project_id: UUID, db: Session = Depends(get_db)
) -> CompanyResources:
    if not settings.SAAS_ENABLED or not settings.SAAS_BILLING_ENABLED:
        raise HTTPException(503, "计量服务尚未启用")
    return resources(db, project_id)


@router.post("/authorize", response_model=UsageAuthorizationResult)
def authorize(
    payload: UsageAuthorization, db: Session = Depends(get_db)
) -> UsageAuthorizationResult:
    if not settings.SAAS_ENABLED or not settings.SAAS_BILLING_ENABLED:
        raise HTTPException(503, "计量服务尚未启用")
    now = datetime.now(timezone.utc)
    account = db.get(CompanyAccount, payload.project_id)
    require_service(account, now)
    if account is None:
        return UsageAuthorizationResult(authorized=True, metered=False)
    row = (
        db.get(AIUsageReservation, payload.reservation_id)
        if payload.reservation_id
        else None
    )
    if (
        row is None
        or row.project_id != payload.project_id
        or row.lease_id != payload.lease_id
        or row.status != "reserved"
        or utc(row.expires_at) <= now
    ):
        raise HTTPException(403, "AI 任务缺少有效额度授权")
    return UsageAuthorizationResult(
        authorized=True, metered=True, platform_model=runtime_model(db)
    )
