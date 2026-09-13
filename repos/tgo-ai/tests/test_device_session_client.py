"""Lifecycle requests must be signed for one project, device and execution."""

from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

import httpx
import pytest
from jose import jwt

from app.config import settings
from app.exceptions import ExternalServiceError
from app.schemas.device_session import DeviceExecutionIdentity
from app.services import device_control_client as module


@pytest.mark.parametrize("operation", ["start", "heartbeat", "finish"])
@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "wrong_session",
        "wrong_device",
        "wrong_agent",
        "expired",
        "wrong_status",
        "denied",
        "redirect",
        "invalid",
        "timeout",
    ],
)
async def test_signed_session_boundary(monkeypatch, operation, case):
    identity = DeviceExecutionIdentity(
        project_id=uuid4(),
        device_id=uuid4(),
        session_id=uuid4(),
        agent_id=uuid4(),
        agent_name="隔离测试",
    )
    seen = []
    original_client = httpx.AsyncClient

    async def handle(request):
        seen.append(request)
        assert request.method == "POST"
        assert request.url.path == (
            f"/v1/sessions/{identity.device_id}/{identity.session_id}/"
            f"{operation}"
        )
        assert json.loads(request.content) == (
            {
                "agent_id": str(identity.agent_id),
                "agent_name": identity.agent_name,
            }
            if operation == "start"
            else {"status": "completed"}
            if operation == "finish"
            else {}
        )
        claims = jwt.decode(
            request.headers["Authorization"][7:],
            settings.secret_key,
            algorithms=["HS256"],
            audience="tgo-device-control",
            issuer="tgo-internal",
        )
        for field in ("project_id", "device_id", "session_id"):
            assert claims[field] == str(getattr(identity, field))
        if case == "timeout":
            raise httpx.ReadTimeout(
                "private transport details", request=request
            )
        if case == "denied":
            return httpx.Response(403, text="private internal details")
        if case == "redirect":
            return httpx.Response(307, headers={"Location": "https://invalid"})
        if case == "invalid":
            return httpx.Response(200, json={"unexpected": True})
        lease = datetime.now(timezone.utc) + timedelta(
            seconds=-1 if case == "expired" else 60
        )
        body = {
            "id": str(
                uuid4() if case == "wrong_session" else identity.session_id
            ),
            "device_id": str(
                uuid4() if case == "wrong_device" else identity.device_id
            ),
            "agent_id": str(
                uuid4() if case == "wrong_agent" else identity.agent_id
            ),
            "status": "completed" if operation == "finish" else "running",
            "lease_expires_at": lease.isoformat(),
        }
        if case == "wrong_status":
            body["status"] = "interrupted"
        return httpx.Response(200, json=body)

    def client(**kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        return original_client(**kwargs, transport=httpx.MockTransport(handle))

    monkeypatch.setattr(module.httpx, "AsyncClient", client)
    call = getattr(module.device_control_client, operation + "_session")
    args = (identity, "completed") if operation == "finish" else (identity,)
    if case == "valid" or (case == "expired" and operation == "finish"):
        await call(*args)
    else:
        with pytest.raises(ExternalServiceError) as error:
            await call(*args)
        assert "private internal" not in str(error.value)
    assert len(seen) == 1
