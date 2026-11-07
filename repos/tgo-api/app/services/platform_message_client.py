"""The shared HTTP transport used by legacy and durable staff delivery."""

from typing import Literal
from uuid import UUID

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, JsonValue, ValidationError

from app.core.config import settings
from app.models.visitor import Visitor
from app.schemas.wecom_durable_status import WeComDurableStatus
from app.services.staff_message_target import (
    PlatformMessageTarget,
    StaffMessageTarget,
)
from app.services.storage import get_storage


class PlatformDeliveryReceipt(BaseModel):
    state: Literal["accepted", "pending", "unknown"]


class NativeHandbackReceipt(BaseModel):
    service_state: Literal[0, 1, 4]


async def prepare_native_mode(
    visitor: Visitor,
    desired_mode: str | None,
) -> None:
    (
        'Require verified native release before committing workbench '
        'AI/assist.'
    )
    if desired_mode not in {"auto", "assist"}:
        return
    platform = visitor.platform
    if (
        platform is None
        or platform.type != "wecom"
        or not (platform.config or {}).get("native_handoff_enabled")
    ):
        return
    token = settings.SAAS_INTERNAL_TOKEN
    if token is None:
        raise HTTPException(503, "企微接待模式同步尚未配置")
    try:
        async with httpx.AsyncClient(
            timeout=settings.PLATFORM_SERVICE_TIMEOUT, trust_env=False
        ) as client:
            response = await client.post(
                settings.PLATFORM_SERVICE_URL.rstrip("/")
                + "/internal/wecom-native/handback",
                headers={"X-SaaS-Service-Token": token.get_secret_value()},
                json={
                    "project_id": str(visitor.project_id),
                    "platform_id": str(visitor.platform_id),
                    "external_userid": visitor.platform_open_id,
                },
            )
        if response.status_code == 409:
            raise HTTPException(409, "请先在企业微信接入并结束排队会话，再切换模式")
        if response.status_code != 200:
            raise HTTPException(503, "企微原生接待尚未释放，模式未切换，请稍后重试")
        NativeHandbackReceipt.model_validate_json(response.content)
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(503, "企微原生接待尚未释放，模式未切换，请稍后重试") from exc


async def read_wecom_durable_status() -> WeComDurableStatus:
    """Fetch a stored-work snapshot through the existing platform transport."""
    token = settings.SAAS_INTERNAL_TOKEN
    if token is None:
        raise HTTPException(503, "企业微信客服监控尚未配置")
    try:
        async with httpx.AsyncClient(
            timeout=settings.PLATFORM_SERVICE_TIMEOUT, trust_env=False
        ) as client:
            response = await client.get(
                settings.PLATFORM_SERVICE_URL.rstrip("/")
                + "/internal/wecom/durable-status",
                headers={"X-SaaS-Service-Token": token.get_secret_value()},
            )
        response.raise_for_status()
        return WeComDurableStatus.model_validate_json(response.content)
    except (httpx.HTTPError, ValidationError) as exc:
        raise HTTPException(503, "企业微信客服监控暂不可用，请稍后重试") from exc


async def get_ai_delivery_receipt(
    project_id: UUID, platform_id: UUID, source_message_id: str
) -> PlatformDeliveryReceipt:
    if settings.SAAS_INTERNAL_TOKEN is None:
        raise RuntimeError("Internal billing credentials are not configured")
    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
        response = await client.post(
            settings.PLATFORM_SERVICE_URL.rstrip("/")
            + "/internal/billing/delivery",
            json={
                "project_id": str(project_id),
                "platform_id": str(platform_id),
                "source_message_id": source_message_id,
            },
            headers={
                "X-SaaS-Service-Token": (
                    settings.SAAS_INTERNAL_TOKEN.get_secret_value()
                )
            },
        )
    response.raise_for_status()
    return PlatformDeliveryReceipt.model_validate_json(response.content)


async def forward_customer_turn(
    *,
    project_id: UUID,
    platform_id: UUID,
    turn_id: UUID,
    version: int,
    channel_id: str,
    source_message_id: str,
) -> None:
    if settings.SAAS_INTERNAL_TOKEN is None:
        raise RuntimeError("Internal channel credentials are not configured")
    async with httpx.AsyncClient(
        timeout=settings.PLATFORM_SERVICE_TIMEOUT, trust_env=False
    ) as client:
        response = await client.post(
            settings.PLATFORM_SERVICE_URL.rstrip("/")
            + "/internal/turns/deliver",
            headers={
                "X-SaaS-Service-Token": (
                    settings.SAAS_INTERNAL_TOKEN.get_secret_value()
                )
            },
            json={
                "project_id": str(project_id),
                "platform_id": str(platform_id),
                "turn_id": str(turn_id),
                "version": version,
                "channel_id": channel_id,
                "source_message_id": source_message_id,
            },
        )
    response.raise_for_status()


def resolve_media_urls(value: JsonValue) -> JsonValue:
    if isinstance(value, list):
        return [resolve_media_urls(item) for item in value]
    if isinstance(value, dict):
        storage = get_storage()
        return {
            key: storage.resolve_url(item)
            if key in ("url", "image_url", "file_url")
            and isinstance(item, str)
            else resolve_media_urls(item)
            for key, item in value.items()
        }
    return value


async def forward_staff_platform_message(
    target: StaffMessageTarget,
    payload: dict[str, JsonValue],
    client_msg_no: str,
) -> httpx.Response:
    return await forward_platform_message(
        target, payload, client_msg_no, from_uid=f"{target.staff_id}-staff"
    )


async def forward_platform_message(
    target: PlatformMessageTarget,
    payload: dict[str, JsonValue],
    client_msg_no: str,
    *,
    from_uid: str,
) -> httpx.Response:
    """One channel transport for authorized staff and queued system notices."""
    headers = {"Content-Type": "application/json"}
    if settings.PLATFORM_SERVICE_API_KEY:
        headers[
            "Authorization"
        ] = f"Bearer {settings.PLATFORM_SERVICE_API_KEY}"
    async with httpx.AsyncClient(
        timeout=settings.PLATFORM_SERVICE_TIMEOUT, trust_env=False
    ) as client:
        return await client.post(
            f"{settings.PLATFORM_SERVICE_URL.rstrip('/')}/v1/messages/send",
            headers=headers,
            json={
                "platform_api_key": target.platform_api_key,
                "from_uid": from_uid,
                "platform_open_id": target.platform_open_id,
                "channel_id": target.channel_id,
                "channel_type": target.channel_type,
                "payload": resolve_media_urls(payload),
                "client_msg_no": client_msg_no,
            },
        )
