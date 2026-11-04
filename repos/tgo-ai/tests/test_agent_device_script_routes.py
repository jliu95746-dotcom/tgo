"""Script configuration accepts only the trusted API identity."""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1 import agent_device_script_bindings as module
from app.config import settings
from app.schemas.agent_device_script_binding import AgentDeviceScriptState


@pytest.mark.parametrize(
    "identity", ["valid", "missing", "public", "wrong_key", "wrong_id"]
)
async def test_configuration_identity_has_no_public_fallback(
    monkeypatch, identity
):
    project, actor, employee = (uuid4() for _ in range(3))
    key = uuid4().hex + uuid4().hex
    monkeypatch.setattr(settings, "secret_key", key)
    service = Mock(
        read=AsyncMock(
            return_value=AgentDeviceScriptState(
                project_id=project,
                agent_id=employee,
                binding=None,
            )
        )
    )
    app = FastAPI()
    app.include_router(module.router, prefix="/api/v1/agents")
    app.dependency_overrides[module.get_service] = lambda: service
    headers = {
        "X-Internal-API-Key": key,
        "X-Project-Id": str(project),
        "X-Staff-Id": str(actor),
    }
    if identity == "missing":
        headers = {}
    elif identity == "public":
        headers = {"Authorization": "Bearer public-test-token"}
    elif identity == "wrong_key":
        headers["X-Internal-API-Key"] = "wrong"
    elif identity == "wrong_id":
        headers["X-Staff-Id"] = "invalid"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        result = await client.get(
            f"/api/v1/agents/{employee}/device-script-binding", headers=headers
        )
    if identity == "valid":
        assert result.status_code == 200
        service.read.assert_awaited_once_with(project, actor, employee)
    else:
        assert result.status_code == 403
        service.read.assert_not_awaited()
