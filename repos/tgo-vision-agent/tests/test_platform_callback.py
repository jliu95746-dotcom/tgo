"""Tests for the tgo-platform callback contract."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.platform_callback import PlatformCallbackService


@pytest.mark.asyncio
async def test_notify_new_message_uses_internal_inbound_contract():
    response = MagicMock()
    response.raise_for_status.return_value = None
    client = AsyncMock()
    client.post.return_value = response
    client_context = AsyncMock()
    client_context.__aenter__.return_value = client

    with patch(
        "app.services.platform_callback.httpx.AsyncClient",
        return_value=client_context,
    ):
        service = PlatformCallbackService(platform_url="http://tgo-platform:8003")
        success = await service.notify_new_message(
            platform_id="platform-1",
            contact_id="wx-user-1",
            contact_name="测试客户",
            message_content="你好",
            message_type="text",
        )

    assert success is True
    _, call_kwargs = client.post.call_args
    assert call_kwargs["json"]["platform_type"] == "wechat_personal"
    assert "X-Platform-API-Key" not in call_kwargs.get("headers", {})
