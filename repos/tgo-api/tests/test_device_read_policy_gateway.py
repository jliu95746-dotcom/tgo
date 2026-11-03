"""Only current tenant may publish a read policy, with conflict passthrough."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from jose import jwt

from app.api.v1.endpoints import device_control
from app.core.config import settings
from app.core.exceptions import ExternalServiceError
from app.schemas.device_read_policy import DeviceReadPolicyPublish, DeviceReadPolicyTest
from app.services.device_control_client import DeviceControlClient


@pytest.mark.asyncio
async def test_read_policy_client_uses_signed_project_and_checks_response(monkeypatch):
    project, device = uuid4(), uuid4()
    observed = []

    def handle(request):
        observed.append(request)
        return httpx.Response(
            200,
            json={
                "device_id": str(device),
                "project_id": str(project),
                "revision": 2,
                "tool_name": "fs_read",
                "allowed_directory": r"C:\Business\Orders",
                "max_lines": 100,
                "max_offset": 10000,
                "digest": "a" * 64,
                "status": "pending",
                "verified": False,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(handle), **kwargs
        ),
    )
    payload = DeviceReadPolicyPublish(
        expected_draft_revision=3, expected_policy_revision=1
    )
    policy = await DeviceControlClient().publish_read_policy(
        device, str(project), payload
    )
    assert policy.revision == 2 and policy.status == "pending"
    assert observed[0].method == "PUT"
    assert observed[0].url.path == f"/v1/devices/{device}/read-policy"
    claims = jwt.decode(
        observed[0].headers["Authorization"][7:],
        settings.SECRET_KEY,
        algorithms=["HS256"],
        audience="tgo-device-control",
        issuer="tgo-internal",
    )
    assert claims["project_id"] == str(project)

    def foreign(_request):
        return httpx.Response(
            200,
            json={
                "device_id": str(device),
                "project_id": str(uuid4()),
                "revision": 2,
                "tool_name": "fs_read",
                "allowed_directory": r"C:\Business\Orders",
                "max_lines": 100,
                "max_offset": 10000,
                "digest": "a" * 64,
                "status": "pending",
                "verified": False,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(foreign), **kwargs
        ),
    )
    with pytest.raises(ExternalServiceError):
        await DeviceControlClient().get_read_policy(device, str(project))


@pytest.mark.asyncio
async def test_gateway_uses_staff_project_and_preserves_conflict(monkeypatch):
    project, device = uuid4(), uuid4()
    payload = DeviceReadPolicyPublish(
        expected_draft_revision=3, expected_policy_revision=1
    )
    publish = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr(
        device_control.device_control_client, "publish_read_policy", publish
    )
    await device_control.publish_device_read_policy(
        device, payload, MagicMock(project_id=project)
    )
    publish.assert_awaited_once_with(device, str(project), payload)

    request = httpx.Request("PUT", "http://device-control.test/v1/devices")
    response = httpx.Response(
        409, json={"detail": "Device read policy changed"}, request=request
    )
    publish.side_effect = httpx.HTTPStatusError(
        "conflict", request=request, response=response
    )
    with pytest.raises(HTTPException) as error:
        await device_control.publish_device_read_policy(
            device, payload, MagicMock(project_id=project)
        )
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_read_test_gateway_uses_staff_project(monkeypatch):
    project, device = uuid4(), uuid4()
    payload = DeviceReadPolicyTest(
        path=r"C:\Business\Orders\proof.txt", expected_text="test-marker"
    )
    test_read = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr(
        device_control.device_control_client, "test_read_policy", test_read
    )
    await device_control.test_device_read_policy(
        device, payload, MagicMock(project_id=project)
    )
    test_read.assert_awaited_once_with(device, str(project), payload)
