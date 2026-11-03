from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from app.services import platform_message_client as transport
from app.services.wecom_native_context import apply_native_state


@pytest.mark.parametrize("desired", ["auto", "assist"])
def test_delayed_native_end_does_not_override_explicit_workbench_mode(desired):
    visitor = SimpleNamespace(service_mode=desired, ai_disabled=desired != "auto")
    apply_native_state(visitor, 4, "", previous_state=3)
    assert visitor.service_mode == desired
    assert visitor.ai_disabled is (desired != "auto")


def visitor_fixture():
    return SimpleNamespace(
        project_id=uuid4(), platform_id=uuid4(), platform_open_id="customer",
        platform=SimpleNamespace(type="wecom", config={"native_handoff_enabled": True}),
    )


def client_fixture(monkeypatch, response):
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.post.return_value = response
    monkeypatch.setattr(transport.httpx, "AsyncClient", Mock(return_value=client))
    monkeypatch.setattr(transport.settings, "SAAS_INTERNAL_TOKEN", SecretStr("test-only-key"))
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "assist"])
async def test_api_waits_for_verified_provider_handback_before_mode_change(monkeypatch, mode):
    client = client_fixture(monkeypatch, httpx.Response(200, json={"service_state": 4}))
    visitor = visitor_fixture()
    await transport.prepare_native_mode(visitor, mode)
    body = client.post.call_args.kwargs["json"]
    assert body == {
        "project_id": str(visitor.project_id),
        "platform_id": str(visitor.platform_id),
        "external_userid": "customer",
    }
    assert client.post.call_args.args[0].endswith("/internal/wecom-native/handback")


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body,expected", [
    (409, {}, 409), (503, {}, 503), (200, {"service_state": 3}, 503),
    (200, {"service_state": 2}, 503), (200, {}, 503),
])
async def test_unconfirmed_or_queued_handoff_blocks_mode_change(monkeypatch, status, body, expected):
    client_fixture(monkeypatch, httpx.Response(status, json=body))
    with pytest.raises(HTTPException) as failure:
        await transport.prepare_native_mode(visitor_fixture(), "auto")
    assert failure.value.status_code == expected


@pytest.mark.asyncio
async def test_network_error_preserves_previous_mode(monkeypatch):
    client = client_fixture(monkeypatch, httpx.Response(200))
    client.post.side_effect = httpx.ConnectError("unavailable")
    with pytest.raises(HTTPException) as failure:
        await transport.prepare_native_mode(visitor_fixture(), "assist")
    assert failure.value.status_code == 503


@pytest.mark.asyncio
async def test_manual_and_other_channels_keep_existing_behavior(monkeypatch):
    client = client_fixture(monkeypatch, httpx.Response(200))
    visitor = visitor_fixture()
    await transport.prepare_native_mode(visitor, "manual")
    visitor.platform.type = "web"
    await transport.prepare_native_mode(visitor, "auto")
    visitor.platform.type = "wecom"
    visitor.platform.config = {}
    await transport.prepare_native_mode(visitor, "assist")
    client.post.assert_not_awaited()
