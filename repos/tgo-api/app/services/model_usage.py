"""Read internal model accounting over HTTP; the AI service owns its database."""

from uuid import UUID

import httpx
from fastapi import HTTPException
from pydantic import TypeAdapter, ValidationError

from app.core.config import settings
from app.schemas.model_usage import ModelUsageResponse


async def list_model_usage(
    project_id: UUID | None, offset: int, limit: int
) -> list[ModelUsageResponse]:
    token = settings.SAAS_INTERNAL_TOKEN
    if token is None:
        raise HTTPException(503, "内部用量服务尚未配置")
    params = {"offset": str(offset), "limit": str(limit)}
    if project_id is not None:
        params["project_id"] = str(project_id)
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            response = await client.get(
                str(settings.AI_SERVICE_URL).rstrip("/")
                + "/api/v1/internal/model-usage",
                params=params,
                headers={"X-SaaS-Service-Token": token.get_secret_value()},
            )
        response.raise_for_status()
        return TypeAdapter(list[ModelUsageResponse]).validate_json(response.content)
    except (httpx.HTTPError, ValidationError) as exc:
        raise HTTPException(503, "模型用量暂不可用，请稍后重试") from exc
