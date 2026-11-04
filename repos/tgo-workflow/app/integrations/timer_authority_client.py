"""Check current API-owned permission without sharing its database."""

import httpx
from app.config import settings
from app.integrations.http_client import HttpClient
from app.schemas.timer_authority import TimerAuthority


async def verify_timer_authority(query: TimerAuthority) -> None:
    key = settings.SECRET_KEY
    if key is None or not key.get_secret_value():
        raise RuntimeError("定时计划内部身份核验未配置。")
    try:
        async with HttpClient.internal_client() as client:
            response = await client.post(
                settings.API_INTERNAL_SERVICE_URL.rstrip("/")
                + "/internal/workflows/schedules/authority/verify",
                json=query.model_dump(mode="json"),
                headers={"X-Internal-API-Key": key.get_secret_value()},
                timeout=5.0,
            )
    except (httpx.HTTPError, OSError, TimeoutError):
        raise RuntimeError("定时计划当前权限暂时无法核验。") from None
    if response.status_code == 409:
        raise PermissionError("定时计划授权人或服务权限已失效。")
    if response.status_code != 200:
        raise RuntimeError("定时计划当前权限暂时无法核验。")
    try:
        receipt = TimerAuthority.model_validate(response.json())
    except ValueError:
        raise RuntimeError("定时计划权限回执无效。") from None
    if receipt != query:
        raise PermissionError("定时计划权限回执与当前来源不一致。")
