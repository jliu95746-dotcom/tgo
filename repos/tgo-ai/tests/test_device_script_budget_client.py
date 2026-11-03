"""Only fresh owned budget observations may inform later script trials."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
from jose import jwt

from app.config import settings
from app.exceptions import ExternalServiceError
from app.services import device_control_client as module


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "exhausted",
        "project",
        "device",
        "policy",
        "grant",
        "stale",
        "future",
        "redirect",
        "denied",
        "private",
        "enable",
        "scope",
        "missing",
        "negative",
        "overcount",
        "remaining",
        "bool_count",
    ],
)
async def test_script_budget_is_observation_only(monkeypatch, case):
    project, device = uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    body = dict(
        scope="script_execution_budget_observation",
        can_enable=False,
        project_id=str(project),
        device_id=str(device),
        connection_instance_id="c" * 32,
        session_id="d" * 32,
        policy_digest="a" * 64,
        grant_digest="b" * 64,
        policy_max_runs=2,
        grant_max_runs=3,
        server_policy_used=1,
        server_grant_used=1,
        device_policy_used=1,
        device_grant_used=1,
        remaining_runs=1,
        observed_at=now.isoformat(),
    )
    if case in {"project", "device"}:
        body[case + "_id"] = str(uuid4())
    elif case in {"policy", "grant"}:
        body[case + "_digest"] = "f" * 64
    elif case == "stale":
        body["observed_at"] = (now - timedelta(minutes=1)).isoformat()
    elif case == "future":
        body["observed_at"] = (now + timedelta(minutes=1)).isoformat()
    elif case == "private":
        body["fields"] = {"secret": "upstream-private"}
    elif case == "enable":
        body["can_enable"] = True
    elif case == "scope":
        body["scope"] = "business_approval"
    elif case == "missing":
        del body["scope"]
    elif case == "negative":
        body["device_policy_used"] = -1
    elif case == "overcount":
        body["server_grant_used"] = 4
    elif case == "remaining":
        body["remaining_runs"] = 2
    elif case == "bool_count":
        body["server_policy_used"] = True
    elif case == "exhausted":
        body.update(
            server_policy_used=2, server_grant_used=2, remaining_runs=0
        )
    calls = []

    async def handle(request):
        calls.append(request)
        assert request.method == "GET" and request.content == b""
        assert request.url.path == f"/v1/devices/{device}/script-budget"
        claims = jwt.decode(
            request.headers["Authorization"][7:],
            settings.secret_key,
            algorithms=["HS256"],
            audience="tgo-device-control",
            issuer="tgo-internal",
        )
        assert claims["sub"] == "tgo-ai"
        assert claims["project_id"] == str(project)
        assert claims["device_id"] == str(device)
        assert (
            "session_id" not in claims and "script_policy_digest" not in claims
        )
        if case == "redirect":
            return httpx.Response(
                307, headers={"location": "https://invalid.example"}
            )
        if case == "denied":
            return httpx.Response(403, text="upstream-private")
        return httpx.Response(200, json=body)

    native = httpx.AsyncClient

    def client(**kwargs):
        assert (
            kwargs["follow_redirects"] is False
            and kwargs["trust_env"] is False
        )
        return native(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", client)
    if case in {"valid", "exhausted"}:
        result = await module.device_control_client.read_script_budget(
            project, device, "a" * 64, "b" * 64
        )
        assert result.can_enable is False
        assert result.remaining_runs == (0 if case == "exhausted" else 1)
    else:
        with pytest.raises(ExternalServiceError) as error:
            await module.device_control_client.read_script_budget(
                project, device, "a" * 64, "b" * 64
            )
        assert "upstream-private" not in str(error.value)
    assert len(calls) == 1
