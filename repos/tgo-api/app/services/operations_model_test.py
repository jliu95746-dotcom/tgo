"""Credential connectivity checks; upstream data and keys stay on the server."""

from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.models import AIProvider
from app.schemas.shared_models import SharedConnectionRequest, SharedConnectionResult
from app.services.billing_quotes import conflict
from app.services.provider_connection import build_test_request
from app.services.shared_models import stored_shared_models
from app.utils.crypto import decrypt_str


async def test_connection(
    db: Session, payload: SharedConnectionRequest
) -> SharedConnectionResult:
    catalogue = stored_shared_models(db)
    if catalogue is None or catalogue.version != payload.expected_version:
        raise conflict("模型配置已变化，请刷新后测试")
    provider = next(
        (item for item in catalogue.providers if item.id == payload.provider_id), None
    )
    if provider is None or not provider.is_active:
        raise conflict("统一模型服务不存在或未启用")
    adapter = AIProvider(
        provider=provider.provider,
        api_base_url=provider.api_base_url,
        config=provider.config,
    )
    method, url, headers = build_test_request(
        adapter, decrypt_str(provider.encrypted_api_key)
    )
    status: int | None = None
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            response = await client.request(method, url, headers=headers)
            status = response.status_code
        success = 200 <= status < 300
        message = "服务连接与凭证校验通过" if success else f"服务返回 HTTP {status}"
    except httpx.RequestError:
        success, message = False, "模型服务连接失败，请检查服务地址和网络"
    return SharedConnectionResult(
        provider_id=provider.id,
        version=catalogue.version,
        success=success,
        http_status=status,
        message=message,
        checked_at=datetime.now(timezone.utc),
    )
