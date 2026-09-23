"""Ask the owning API service before starting any billable model execution."""

from secrets import compare_digest
from contextvars import ContextVar
from uuid import UUID

import httpx
from fastapi import Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.config import settings
from app.dependencies import get_current_or_internal_project_id
from app.schemas.platform_models import PlatformModelRuntime

metered_execution: ContextVar[bool] = ContextVar("metered_ai_execution", default=False)


class UsageAuthorization(BaseModel):
    project_id: UUID
    reservation_id: UUID | None = None
    lease_id: UUID | None = None


current_authorization: ContextVar[UsageAuthorization | None] = ContextVar(
    "current_quota_authorization", default=None
)
current_platform_model: ContextVar[PlatformModelRuntime | None] = ContextVar(
    "platform_model_credentials", default=None
)


class AuthorizationResult(BaseModel):
    authorized: bool
    metered: bool
    platform_model: PlatformModelRuntime | None = None


async def authorize(request: Request, project_id: UUID) -> None:
    metered_execution.set(False)
    current_authorization.set(None)
    current_platform_model.set(None)
    if not settings.saas_enabled or not settings.saas_billing_enabled:
        return
    token = settings.saas_internal_token
    received = request.headers.get("X-SaaS-Service-Token", "")
    if (
        token is None
        or not received
        or not compare_digest(received, token.get_secret_value())
    ):
        raise HTTPException(403, "AI 调用必须由企业额度服务授权")
    try:
        reservation, lease = request.headers.get(
            "X-AI-Reservation"
        ), request.headers.get("X-AI-Lease")
        payload = UsageAuthorization(
            project_id=project_id,
            reservation_id=UUID(reservation) if reservation else None,
            lease_id=UUID(lease) if lease else None,
        )
    except ValueError as exc:
        raise HTTPException(403, "AI 额度授权无效") from exc
    try:
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            response = await client.post(
                settings.api_internal_service_url.rstrip("/")
                + "/internal/billing/usage/authorize",
                json=payload.model_dump(mode="json"),
                headers={"X-SaaS-Service-Token": token.get_secret_value()},
            )
        if response.status_code in {402, 403}:
            raise HTTPException(response.status_code, "套餐或 AI 次数不足，请由管理员检查订阅")
        response.raise_for_status()
        result = AuthorizationResult.model_validate_json(response.content)
        if not result.authorized:
            raise HTTPException(403, "AI 额度授权被拒绝")
        metered_execution.set(result.metered)
        if result.metered:
            current_authorization.set(payload)
            current_platform_model.set(result.platform_model)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "额度服务暂不可用，已暂停新 AI 调用") from exc


async def require_reply_quota(request: Request, project_id: UUID = Query(...)) -> None:
    await authorize(request, project_id)


async def require_internal_reply_quota(
    request: Request, project_id: UUID = Depends(get_current_or_internal_project_id)
) -> None:
    await authorize(request, project_id)
