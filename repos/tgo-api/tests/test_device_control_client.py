"""Every gateway device request carries the authenticated project's scope."""

from uuid import uuid4
from datetime import datetime, timezone

import httpx
from jose import jwt
import pytest

from app.core.config import settings
from app.services.device_control_client import DeviceControlClient
from app.schemas.device_scenario import DeviceScenario, DeviceScenarioDraftWrite
from app.core.exceptions import ExternalServiceError


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["list", "get", "bind", "patch", "delete", "disconnect", "tools", "connected"])
async def test_device_requests_are_signed_and_use_current_routes(monkeypatch, operation):
    project, device = str(uuid4()), str(uuid4())
    observed = []

    def handle(request):
        observed.append(request)
        return httpx.Response(200, json={"devices": [], "count": 0, "tools": [], "success": True})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(handle), **kwargs))
    client = DeviceControlClient()
    if operation == "list":
        await client.list_devices(project)
    elif operation == "get":
        await client.get_device(device, project)
    elif operation == "bind":
        await client.generate_bind_code(project)
    elif operation == "patch":
        await client.update_device(device, project, {"device_name": "Fixture"})
    elif operation == "delete":
        await client.delete_device(device, project)
    elif operation == "disconnect":
        await client.disconnect_device(device, project)
    elif operation == "tools":
        await client.get_device_tools(device, project)
        assert observed[0].url.path == f"/v1/mcp/tools/{device}"
    else:
        await client.list_connected_devices(project)
        assert observed[0].url.path == "/v1/devices/connected"
    assert len(observed) == 1
    header = observed[0].headers.get("Authorization", "")
    assert header.startswith("Bearer ")
    claims = jwt.decode(header[7:], settings.SECRET_KEY, algorithms=["HS256"],
                        audience="tgo-device-control", issuer="tgo-internal")
    assert claims["sub"] == "tgo-api" and claims["project_id"] == project
    assert 0 < claims["exp"] - claims["iat"] <= 300


@pytest.mark.asyncio
async def test_device_scenario_draft_is_scoped_and_uses_revision(monkeypatch):
    project, device = uuid4(), uuid4()
    observed = []

    def handle(request):
        observed.append(request)
        return httpx.Response(200, json={
            "device_id": str(device), "project_id": str(project),
            "scenario": "files", "purpose": "文件整理", "target_directory": "C:\\Work",
            "revision": 2, "status": "draft", "updated_at": datetime.now(timezone.utc).isoformat(),
        })

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(handle), **kwargs))
    client = DeviceControlClient()
    draft = await client.save_scenario_draft(
        device, DeviceScenario.FILES, str(project),
        DeviceScenarioDraftWrite(expected_revision=1, purpose="文件整理", target_directory="C:\\Work"),
    )
    assert draft.revision == 2 and draft.status == "draft"
    assert observed[0].url.path == f"/v1/devices/{device}/scenario-drafts/files"
    assert observed[0].method == "PUT"
    assert __import__("json").loads(observed[0].content)["expected_revision"] == 1
    header = observed[0].headers["Authorization"]
    claims = jwt.decode(header[7:], settings.SECRET_KEY, algorithms=["HS256"],
                        audience="tgo-device-control", issuer="tgo-internal")
    assert claims["project_id"] == str(project)


@pytest.mark.asyncio
async def test_device_scenario_draft_rejects_other_project_response(monkeypatch):
    project, device = uuid4(), uuid4()

    def handle(_request):
        return httpx.Response(200, json={
            "device_id": str(device), "project_id": str(uuid4()),
            "scenario": "files", "purpose": "错误租户", "target_directory": None,
            "revision": 1, "status": "draft", "updated_at": datetime.now(timezone.utc).isoformat(),
        })

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(handle), **kwargs))
    with pytest.raises(ExternalServiceError):
        await DeviceControlClient().get_scenario_draft(device, DeviceScenario.FILES, str(project))
