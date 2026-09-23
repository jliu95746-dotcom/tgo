"""Commercial mode accepts IM callbacks only on the internal application."""

from unittest.mock import AsyncMock

import httpx
import pytest

from app.api.v1.endpoints import wukongim_webhook
from app.core.config import settings
from app.internal import internal_app
from app.main import app


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["msg.notify", "user.onlinestatus"])
async def test_saas_public_webhook_cannot_enqueue_or_mutate(event, monkeypatch):
    monkeypatch.setattr(settings, "SAAS_ENABLED", True)
    online, messages = AsyncMock(), AsyncMock()
    monkeypatch.setattr(wukongim_webhook, "_process_user_online_status", online)
    monkeypatch.setattr(wukongim_webhook, "_process_msg_notify", messages)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        result = await client.post(
            "/v1/integrations/wukongim/webhook", params={"event": event},
            json=[{"uid": "spoofed-staff", "payload": {"content": "fake"}}],
        )
        assert result.status_code == 404
    online.assert_not_awaited()
    messages.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["msg.notify", "user.onlinestatus"])
async def test_internal_callback_delivers_existing_event_format(event, monkeypatch):
    monkeypatch.setattr(settings, "SAAS_ENABLED", True)
    online, messages = AsyncMock(), AsyncMock()
    monkeypatch.setattr(wukongim_webhook, "_process_user_online_status", online)
    monkeypatch.setattr(wukongim_webhook, "_process_msg_notify", messages)
    body = [{"uid": "test-staff", "is_online": True}]
    path = "/internal/integrations/wukongim/webhook"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=internal_app), base_url="http://internal",
    ) as client:
        result = await client.post(path, params={"event": event}, json=body)
        assert result.status_code == 200
        assert result.json() == {"code": 0, "message": "ok"}
    (messages if event == "msg.notify" else online).assert_awaited_once_with(body)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        assert (await client.post(path, params={"event": event}, json=body)).status_code == 404


@pytest.mark.asyncio
async def test_legacy_public_callback_remains_available_before_saas_switch(monkeypatch):
    monkeypatch.setattr(settings, "SAAS_ENABLED", False)
    messages = AsyncMock()
    monkeypatch.setattr(wukongim_webhook, "_process_msg_notify", messages)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        result = await client.post(
            "/v1/integrations/wukongim/webhook", params={"event": "msg.notify"}, json=[],
        )
    assert result.status_code == 200
    messages.assert_awaited_once_with([])
