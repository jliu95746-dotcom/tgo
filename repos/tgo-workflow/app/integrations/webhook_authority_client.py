"""Recheck current permission without reading the gateway's database."""

import httpx
from app.config import settings
from app.integrations.http_client import HttpClient
from app.schemas.webhook_authority import WebhookAuthority


async def verify_webhook_authority(query: WebhookAuthority) -> None:
    key = settings.SECRET_KEY
    if key is None or not key.get_secret_value():
        raise RuntimeError("外部触发内部身份核验未配置。")
    try:
        async with HttpClient.internal_client() as client:
            response = await client.post(
                settings.API_INTERNAL_SERVICE_URL.rstrip("/")
                + "/internal/workflows/webhooks/authority/verify",
                json=query.model_dump(mode="json"),
                headers={"X-Internal-API-Key": key.get_secret_value()},
                timeout=5.0,
            )
    except (httpx.HTTPError, OSError, TimeoutError):
        raise RuntimeError("外部触发当前权限暂时无法核验。") from None
    if response.status_code == 409:
        raise PermissionError("外部触发授权人或当前服务权限已失效。")
    if response.status_code != 200:
        raise RuntimeError("外部触发当前权限暂时无法核验。")
    try:
        receipt = WebhookAuthority.model_validate(response.json())
    except ValueError:
        raise RuntimeError("外部触发权限回执无效。") from None
    if receipt != query:
        raise PermissionError("外部触发权限回执与当前来源不一致。")
