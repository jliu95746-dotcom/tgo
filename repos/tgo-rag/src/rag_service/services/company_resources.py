"""Obtain company authorization over HTTP and lock local source-byte capacity."""

from datetime import datetime
from uuid import UUID

import httpx
from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import File, QAPair


class CompanyResources(BaseModel):
    metered: bool
    knowledge_bytes: int | None
    channel_limit: int | None
    expires_at: datetime | None


async def require_processing(project_id: UUID) -> CompanyResources:
    settings = get_settings()
    if not settings.saas_enabled or not settings.saas_billing_enabled:
        return CompanyResources(
            metered=False, knowledge_bytes=None, channel_limit=None, expires_at=None
        )
    if settings.saas_internal_token is None or not settings.saas_api_internal_url:
        raise HTTPException(503, "企业额度服务未配置，已暂停知识处理")
    try:
        async with httpx.AsyncClient(
            timeout=10, trust_env=False, follow_redirects=False
        ) as client:
            response = await client.get(
                settings.saas_api_internal_url.rstrip("/")
                + f"/internal/billing/usage/resources/{project_id}",
                headers={
                    "X-SaaS-Service-Token": settings.saas_internal_token.get_secret_value()
                },
            )
        if response.status_code == 402:
            raise HTTPException(402, "企业套餐已到期或暂停，不能开始新的知识处理任务")
        response.raise_for_status()
        return CompanyResources.model_validate(response.json())
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "企业额度服务暂不可用，已暂停知识处理") from exc


async def ensure_capacity(
    db: AsyncSession, project_id: UUID, additional_bytes: int = 0
) -> None:
    limits = await require_processing(project_id)
    if limits.knowledge_bytes is None:
        return
    # All source creation/update callers hold the same transaction lock until commit.
    # UUID conversion is stable and avoids dependence on process-randomized hash().
    lock_key = int.from_bytes(project_id.bytes[:8], "big", signed=True)
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    await db.flush()
    files = await db.scalar(
        select(func.coalesce(func.sum(File.file_size), 0)).where(
            File.project_id == project_id, File.deleted_at.is_(None)
        )
    )
    qa = await db.scalar(
        select(
            func.coalesce(
                func.sum(
                    func.octet_length(QAPair.question)
                    + func.octet_length(QAPair.answer)
                ),
                0,
            )
        ).where(QAPair.project_id == project_id, QAPair.deleted_at.is_(None))
    )
    if (
        int(files or 0) + int(qa or 0) + max(0, additional_bytes)
        > limits.knowledge_bytes
    ):
        raise HTTPException(409, "知识库容量已达到套餐上限，请清理资料或升级套餐")
