"""Server-time service gates; expired administrators can still read and renew."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.models import VisitorSession
from app.models.company_account import CompanyAccount
from app.services.ai_usage import require_service
from app.services.company_email import utc


def require_new_service(db: Session, project_id: UUID) -> None:
    if settings.SAAS_ENABLED and settings.SAAS_BILLING_ENABLED:
        require_service(db.get(CompanyAccount, project_id), datetime.now(timezone.utc))


def new_service_available(db: Session, project_id: UUID) -> bool:
    """Expose a presentation flag without leaking plan, usage or company data."""
    try:
        require_new_service(db, project_id)
        return True
    except TGOAPIException as exc:
        if exc.code != "SUBSCRIPTION_EXPIRED":
            raise
        return False


def require_human_service(
    db: Session, project_id: UUID, visitor_id: UUID | None
) -> None:
    if not settings.SAAS_ENABLED or not settings.SAAS_BILLING_ENABLED:
        return
    account = db.get(CompanyAccount, project_id)
    now = datetime.now(timezone.utc)
    if account is None:
        return
    if (
        account.status in {"trial", "active"}
        and account.expires_at
        and utc(account.expires_at) > now
    ):
        return
    if (
        visitor_id is not None
        and account.status in {"trial", "active", "expired"}
        and account.expires_at is not None
        and now < utc(account.expires_at) + timedelta(hours=24)
    ):
        session = db.scalar(
            select(VisitorSession.id)
            .where(
                VisitorSession.project_id == project_id,
                VisitorSession.visitor_id == visitor_id,
                VisitorSession.status == "open",
                VisitorSession.staff_id.is_not(None),
                VisitorSession.created_at < account.expires_at,
            )
            .limit(1)
        )
        if session is not None:
            return
    raise TGOAPIException(
        "企业服务已暂停，请由管理员续费", code="SUBSCRIPTION_EXPIRED", status_code=402
    )
