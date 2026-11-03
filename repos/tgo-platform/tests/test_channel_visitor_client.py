"""Actual HTTP client keeps service credentials private and rechecks source scope."""

from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import settings
from app.infra.visitor_client import VisitorService


@pytest.mark.asyncio
async def test_channel_resolution_uses_private_auth_and_does_not_cache_grants(
    monkeypatch,
):
    token = "fixture-only-private-service-token"
    monkeypatch.setattr(settings, "saas_internal_token", SecretStr(token))
    calls = []

    def handle(request):
        calls.append(request)
        assert request.url.path == "/internal/channel-visitors/register"
        assert request.headers["X-SaaS-Service-Token"] == token
        assert token not in request.content.decode()
        return httpx.Response(
            201,
            json={
                "id": "visitor",
                "project_id": "project",
                "platform_id": "platform",
                "platform_type": "wecom",
                "platform_open_id": "CaseSensitiveUser",
                "created_at": "2026-10-12T00:00:00Z",
                "updated_at": "2026-10-12T00:00:00Z",
                "first_visit_time": "2026-10-12T00:00:00Z",
                "last_visit_time": "2026-10-12T00:00:00Z",
                "is_online": False,
                "channel_id": "visitor-vtr",
                "channel_type": 251,
                "im_token": "must-not-retain",
                "visitor_access_token": "must-not-retain",
            },
        )

    service = VisitorService(base_url="http://owned-internal.test")
    await service._client.aclose()
    service._client = httpx.AsyncClient(
        base_url="http://owned-internal.test", transport=httpx.MockTransport(handle)
    )
    service._redis = AsyncMock()
    try:
        for _ in range(2):
            info = await service.register_or_get(
                "public-key", "project", "wecom", "CaseSensitiveUser"
            )
            assert "must-not-retain" not in info.model_dump_json()
        assert len(calls) == 2
        assert service._redis.get.await_count == 0
        assert "must-not-retain" not in str(service._redis.set.call_args)
        assert VisitorService.make_cache_key(
            "PROJECT", "WECOM", "CaseSensitiveUser"
        ).endswith(":CaseSensitiveUser")
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_missing_service_credentials_cannot_use_an_old_cache(monkeypatch):
    monkeypatch.setattr(settings, "saas_internal_token", None)
    service = VisitorService(base_url="http://owned-internal.test")
    service._redis = AsyncMock()
    try:
        with pytest.raises(RuntimeError, match="内部渠道服务凭据"):
            await service.register_or_get(
                "public-key", "project", "wecom", "external-user"
            )
        assert (
            service._redis.get.await_count == 0 and service._redis.set.await_count == 0
        )
    finally:
        await service.aclose()
