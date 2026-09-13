"""The shared HTTP transport used by legacy and durable staff delivery."""

import httpx
from pydantic import JsonValue

from app.core.config import settings
from app.services.staff_message_target import PlatformMessageTarget, StaffMessageTarget
from app.services.storage import get_storage


def resolve_media_urls(value: JsonValue) -> JsonValue:
    if isinstance(value, list):
        return [resolve_media_urls(item) for item in value]
    if isinstance(value, dict):
        storage = get_storage()
        return {
            key: storage.resolve_url(item)
            if key in ("url", "image_url", "file_url") and isinstance(item, str)
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
        headers["Authorization"] = f"Bearer {settings.PLATFORM_SERVICE_API_KEY}"
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
