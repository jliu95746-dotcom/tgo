"""Device management and MCP need signed project/device-scoped service access."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
from jose import jwt
import pytest
import pytest_asyncio

from app.config import settings
from app.core.database import get_async_db
from app.main import app
from app.schemas.device import DeviceResponse
from app.services.device_service import DeviceService
from app.services.mcp_server import mcp_proxy

KEY = "test-only-device-capability-key"


def token(project, device_id=None, caller="tgo-api", **changes):
    now = datetime.now(timezone.utc)
    claims = {
        "sub": caller,
        "iss": "tgo-internal",
        "aud": "tgo-device-control",
        "project_id": str(project),
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    if device_id is not None:
        claims["device_id"] = str(device_id)
    claims.update(changes)
    return {"Authorization": "Bearer " + jwt.encode(claims, KEY, algorithm="HS256")}


@pytest_asyncio.fixture
async def access(monkeypatch):
    project, device = uuid4(), uuid4()
    monkeypatch.setattr(settings, "SECRET_KEY", KEY)
    row = DeviceResponse(
        id=device,
        project_id=project,
        device_name="Fixture",
        os="test",
        status="online",
        created_at=datetime.now(timezone.utc),
    )

    async def get_device(self, device_id, project_id):
        return row if device_id == device and project_id == project else None

    monkeypatch.setattr(DeviceService, "get_device", get_device)
    monkeypatch.setattr(DeviceService, "list_devices", AsyncMock(return_value=([], 0)))
    monkeypatch.setattr(DeviceService, "generate_bind_code", AsyncMock(return_value=("ABC123", row.created_at)))
    forwarded = AsyncMock(return_value={"jsonrpc": "2.0", "id": 1, "result": {"tools": []}})
    monkeypatch.setattr(mcp_proxy, "handle_jsonrpc", forwarded)

    async def db():
        yield MagicMock()

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_async_db] = db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, project, device, forwarded
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["missing", "expired", "audience", "issuer", "caller", "project", "no_expiry"])
async def test_invalid_service_identity_cannot_call_device(access, kind):
    client, project, device, forwarded = access
    changes = {
        "expired": {"exp": datetime.now(timezone.utc) - timedelta(seconds=5)},
        "audience": {"aud": "staff-login"},
        "issuer": {"iss": "other"},
        "caller": {"sub": "staff"},
        "project": {"project_id": "bad"},
        "no_expiry": {"exp": None},
    }
    headers = {} if kind == "missing" else token(project, **changes[kind])
    response = await client.post(f"/mcp/{device}", json={"id": 1, "method": "tools/list"}, headers=headers)
    assert response.status_code == 401
    forwarded.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/devices", "/v1/devices/bind-code", "/debug/status"])
async def test_management_and_debug_require_identity(access, path):
    client, project, _, _ = access
    response = await client.request(
        "POST" if path.endswith("bind-code") else "GET", path, params={"project_id": str(project)}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_query_project_cannot_override_signed_scope(access):
    client, project, _, _ = access
    response = await client.get("/v1/devices", params={"project_id": str(uuid4())}, headers=token(project))
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_api_management_uses_signed_project_without_query(access):
    client, project, _, _ = access
    response = await client.get("/v1/devices", headers=token(project))
    assert response.status_code == 200
    assert response.json() == {"devices": [], "total": 0}


@pytest.mark.asyncio
async def test_foreign_project_device_is_not_discovered(access):
    client, _, device, forwarded = access
    response = await client.post(f"/mcp/{device}", json={"id": 1, "method": "tools/list"}, headers=token(uuid4()))
    assert response.status_code == 404
    forwarded.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("scoped_device", [None, "other"])
async def test_ai_must_match_its_exact_bound_device(access, scoped_device):
    client, project, device, forwarded = access
    headers = token(project, uuid4() if scoped_device else None, caller="tgo-ai")
    response = await client.post(f"/mcp/{device}", json={"id": 1, "method": "tools/list"}, headers=headers)
    assert response.status_code in (401, 403)
    forwarded.assert_not_awaited()


@pytest.mark.asyncio
async def test_ai_bound_device_can_be_read_and_used_but_not_manage_project(access):
    client, project, device, forwarded = access
    headers = token(project, device, caller="tgo-ai")
    response = await client.get(f"/v1/devices/{device}", headers=headers)
    assert response.status_code == 200
    response = await client.post(f"/mcp/{device}", json={"id": 1, "method": "tools/list"}, headers=headers)
    assert response.status_code == 200
    forwarded.assert_awaited_once()
    for method, path in (
        ("GET", "/v1/devices"),
        ("POST", "/v1/devices/bind-code"),
        ("DELETE", f"/v1/devices/{device}"),
        ("GET", "/debug/status"),
    ):
        response = await client.request(method, path, headers=headers)
        assert response.status_code == 403, (method, path, response.status_code)
