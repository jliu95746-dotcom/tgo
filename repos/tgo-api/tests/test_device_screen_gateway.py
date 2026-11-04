"""Gateway ownership, signed scope and conflict preservation."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.endpoints import device_screen_policies
from app.core.exceptions import ExternalServiceError
from app.core.security import get_current_active_user
from app.schemas.device_screen_publication import ScreenPolicyPublish
from app.services.device_control_client import DeviceControlClient


def payload():
    return ScreenPolicyPublish(
        expected_policy_revision=0,
        connection_instance_id="c" * 32,
        grant_id="a" * 32,
        max_actions=5,
        allow_click=False,
        allow_text=False,
        click_regions=[],
    )


def response(project, device):
    return {
        "device_id": str(device),
        "project_id": str(project),
        "status": "pending",
        "verified": False,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "policy": {
            "protocol_version": 1,
            "revision": 1,
            "grant_id": "a" * 32,
            "max_actions": 5,
            "allow_click": False,
            "allow_text": False,
            "click_regions": [],
            "digest": "d" * 64,
        },
    }


@pytest.mark.asyncio
async def test_screen_client_preserves_route_and_never_trusts_foreign_response(
    monkeypatch,
):
    project, device = uuid4(), uuid4()
    client = DeviceControlClient()
    send = AsyncMock(return_value=response(project, device))
    monkeypatch.setattr(client, "_request", send)
    await client.publish_screen_policy(device, str(project), payload())
    assert send.await_args.args == (
        "PUT",
        f"/v1/devices/{device}/screen-policy",
        str(project),
    )
    assert send.await_args.kwargs["json"] == payload().model_dump(mode="json")
    send.return_value = response(uuid4(), device)
    with pytest.raises(ExternalServiceError):
        await client.get_screen_policy(device, str(project))
    send.return_value = response(project, uuid4())
    with pytest.raises(ExternalServiceError):
        await client.get_screen_policy(device, str(project))


@pytest.mark.asyncio
async def test_non_admin_cannot_read_grant_publish_or_revoke(monkeypatch):
    app = FastAPI()
    app.include_router(device_screen_policies.router)
    app.dependency_overrides[
        get_current_active_user
    ] = lambda: SimpleNamespace(role="agent", project_id=uuid4())
    send = AsyncMock()
    monkeypatch.setattr(
        device_screen_policies.device_control_client, "_request", send
    )
    device = uuid4()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for method, suffix, body in (
            ("GET", "screen-grant", None),
            ("GET", "screen-policy", None),
            ("PUT", "screen-policy", payload().model_dump(mode="json")),
            ("POST", "screen-policy/revoke", {"expected_policy_revision": 1}),
            ("POST", "read-policy/revoke", {"expected_policy_revision": 1}),
        ):
            result = await client.request(
                method, f"/devices/{device}/{suffix}", json=body
            )
            assert result.status_code == 403
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_gateway_uses_admin_project_and_keeps_409(monkeypatch):
    project, device = uuid4(), uuid4()
    app = FastAPI()
    app.include_router(device_screen_policies.router)
    app.dependency_overrides[
        get_current_active_user
    ] = lambda: SimpleNamespace(role="admin", project_id=project)
    send = AsyncMock(return_value=response(project, device))
    monkeypatch.setattr(
        device_screen_policies.device_control_client, "_request", send
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.put(
            f"/devices/{device}/screen-policy",
            json=payload().model_dump(mode="json"),
        )
        assert result.status_code == 200 and result.json()["verified"] is False
        assert send.await_args.args[2] == str(project)
        http_request = httpx.Request(
            "PUT", "http://fixture.invalid/screen-policy"
        )
        conflict = httpx.Response(
            409,
            json={"detail": "Device screen policy changed"},
            request=http_request,
        )
        send.side_effect = httpx.HTTPStatusError(
            "conflict", request=http_request, response=conflict
        )
        assert (
            await client.put(
                f"/devices/{device}/screen-policy",
                json=payload().model_dump(mode="json"),
            )
        ).status_code == 409
