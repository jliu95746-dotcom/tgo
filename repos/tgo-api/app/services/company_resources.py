"""Resolve sold capacity snapshots and serialize channel creation."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.models import Platform
from app.models.billing import SubscriptionPeriod
from app.models.company_account import CompanyAccount
from app.schemas.billing import PlanDefinition
from app.schemas.company_resources import CompanyResources
from app.services.ai_usage import require_service
from app.services.company_membership import lock_company


def resources(db: Session, project_id: UUID) -> CompanyResources:
    account = db.get(CompanyAccount, project_id)
    now = datetime.now(timezone.utc)
    require_service(account, now)
    if account is None:
        return CompanyResources(
            metered=False, knowledge_bytes=None, channel_limit=None, expires_at=None
        )
    if account.status == "trial":
        return CompanyResources(
            metered=True,
            knowledge_bytes=settings.SAAS_TRIAL_KNOWLEDGE_BYTES,
            channel_limit=settings.SAAS_TRIAL_CHANNELS,
            expires_at=account.expires_at,
        )
    period = db.scalar(
        select(SubscriptionPeriod).where(
            SubscriptionPeriod.project_id == project_id,
            SubscriptionPeriod.starts_at <= now,
            SubscriptionPeriod.ends_at > now,
        )
    )
    if period is None:
        raise TGOAPIException(
            "订阅权益记录异常，请联系平台处理", code="SUBSCRIPTION_INCONSISTENT", status_code=409
        )
    definition = PlanDefinition.model_validate(period.definition)
    return CompanyResources(
        metered=True,
        knowledge_bytes=definition.knowledge_bytes,
        channel_limit=definition.channel_limit,
        expires_at=account.expires_at,
    )


def reserve_channel_creation(db: Session, project_id: UUID) -> None:
    if not settings.SAAS_ENABLED or not settings.SAAS_BILLING_ENABLED:
        return
    lock_company(db, project_id)
    limits = resources(db, project_id)
    if limits.channel_limit is None:
        return
    used = (
        db.scalar(
            select(func.count())
            .select_from(Platform)
            .where(Platform.project_id == project_id, Platform.deleted_at.is_(None))
        )
        or 0
    )
    if used >= limits.channel_limit:
        raise TGOAPIException(
            "渠道数量已达到套餐上限，请先升级套餐", code="CHANNEL_LIMIT_EXCEEDED", status_code=409
        )
