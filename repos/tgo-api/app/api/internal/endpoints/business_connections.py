"""Only the business adapter can resolve exact-version test credentials."""

import hmac
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.staff import Staff
from app.schemas.business_connections import (
    BusinessConnectionPrivateSnapshot,
    BusinessConnectionResolutionRequest,
)
from app.services import business_connections

router = APIRouter()


def require_business_adapter(
    x_business_service_token: str | None = Header(default=None),
) -> None:
    configured = settings.BUSINESS_CONNECTION_SERVICE_TOKEN
    if configured is None or len(configured.get_secret_value()) < 32:
        raise HTTPException(503, "业务连接私有服务尚未配置")
    if not x_business_service_token or not hmac.compare_digest(
        configured.get_secret_value().encode(), x_business_service_token.encode()
    ):
        raise HTTPException(403, "业务连接私有鉴权失败")


@router.post(
    "/{connection_id}/resolve",
    response_model=BusinessConnectionPrivateSnapshot,
    dependencies=[Depends(require_business_adapter)],
)
def resolve_connection(
    connection_id: UUID,
    payload: BusinessConnectionResolutionRequest,
    db: Session = Depends(get_db),
) -> BusinessConnectionPrivateSnapshot:
    actor = db.execute(
        select(Staff)
        .where(Staff.id == payload.actor_id, Staff.project_id == payload.project_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if (
        actor is None
        or actor.role != "admin"
        or actor.deleted_at is not None
        or not actor.account_enabled
    ):
        raise HTTPException(403, "当前身份无权核验此企业连接")
    connection, credentials = business_connections.resolve(
        db,
        payload.project_id,
        connection_id,
        expected_revision=payload.expected_revision,
    )
    return BusinessConnectionPrivateSnapshot(
        connection=connection, credentials=credentials
    )
