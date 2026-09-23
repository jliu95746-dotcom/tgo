"""Private usage inventory for the platform operator proxy only."""

from secrets import compare_digest
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.model_usage import ModelUsageRecord
from app.schemas.model_usage import ModelUsageResponse


async def require_usage_reader(request: Request) -> None:
    if not settings.saas_enabled:
        raise HTTPException(404, "用量统计尚未启用")
    token = settings.saas_internal_token
    received = request.headers.get("X-SaaS-Service-Token", "")
    if (
        token is None
        or not received
        or not compare_digest(received, token.get_secret_value())
    ):
        raise HTTPException(403, "仅允许内部运营服务读取模型用量")


router = APIRouter(dependencies=[Depends(require_usage_reader)])


@router.get("", response_model=list[ModelUsageResponse])
async def records(
    project_id: UUID | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> list[ModelUsageRecord]:
    statement = select(ModelUsageRecord).where(ModelUsageRecord.deleted_at.is_(None))
    if project_id is not None:
        statement = statement.where(ModelUsageRecord.project_id == project_id)
    result = await db.scalars(
        statement.order_by(ModelUsageRecord.created_at.desc(), ModelUsageRecord.id)
        .offset(offset)
        .limit(limit)
    )
    return list(result)
