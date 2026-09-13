"""Transport errors and diagnostics must not masquerade as success or leak keys."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from app.config import settings
from app.main import app
from app.services import mcp_server, tcp_rpc_server
from app.services.bind_code_service import bind_code_service
from app.core.device_auth import require_bound_device, require_management_project, require_service_principal
from app.schemas.device_access import DeviceServicePrincipal


@pytest.fixture
def scoped_http():
    project = uuid4()
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[require_management_project] = lambda: project
    app.dependency_overrides[require_bound_device] = lambda: None
    app.dependency_overrides[require_service_principal] = lambda: DeviceServicePrincipal(sub="tgo-api", project_id=project)
    yield project
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)


@pytest.mark.asyncio
async def test_health_is_unavailable_when_tcp_listener_is_down(monkeypatch):
    monkeypatch.setattr(tcp_rpc_server.tcp_rpc_server, "server", None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health")
    assert response.status_code == 503
    assert response.json()["status"] == "unhealthy"


@pytest.mark.asyncio
async def test_debug_does_not_expose_credentials_or_bind_codes(monkeypatch, scoped_http):
    redis = AsyncMock()
    redis.keys.return_value = ["dc:bind_code:ABC123"]
    redis.get.return_value = str(scoped_http)

    async def scan(**kwargs):
        yield "dc:bind_code:ABC123"

    redis.scan_iter = scan
    monkeypatch.setattr(bind_code_service, "redis", redis)
    monkeypatch.setattr(
        settings, "REDIS_URL", "redis://user:private-password@localhost:6379"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/debug/status")
    assert response.status_code == 200
    assert "ABC123" not in response.text
    assert "private-password" not in response.text
    assert response.json()["redis"]["active_bind_codes"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [[], None, "text"])
async def test_non_object_rpc_is_explicit_invalid_request(body, scoped_http):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/mcp/{uuid4()}", content=__import__("json").dumps(body)
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32600


@pytest.mark.asyncio
async def test_device_rpc_error_is_forwarded_as_error_not_success(monkeypatch):
    error = {"code": -32602, "message": "Invalid fixture arguments"}
    connection = MagicMock()
    connection.call_tool = AsyncMock(return_value={"error": error})
    monkeypatch.setattr(
        mcp_server.tcp_connection_manager, "get_connection", lambda _: connection
    )
    response = await mcp_server.mcp_proxy.handle_jsonrpc(
        "test",
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "fixture"},
        },
    )
    assert response == {"jsonrpc": "2.0", "id": 7, "error": error}
