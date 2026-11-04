"""Only owned, acknowledged readonly metadata may become an employee pin."""

import httpx
import pytest
from jose import jwt
from uuid import uuid4

from app.config import settings
from app.exceptions import ExternalServiceError
from app.services import device_control_client as module
from tests.test_agent_device_script_binding import Owner


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "project",
        "device",
        "write",
        "pending",
        "redirect",
        "error",
        "malformed",
        "missing_script",
        "extra_catalog",
        "grant",
        "duplicate",
    ],
)
async def test_script_metadata_is_bound_and_never_executes(monkeypatch, case):
    project, device = uuid4(), uuid4()
    bound, item = await Owner().validate_script_configuration(project, device)
    if case in {"project", "device"}:
        setattr(bound, case + "_id", uuid4())
    elif case == "pending":
        bound.status = "pending"
    elif case == "write":
        item.effect = "write"
    elif case == "grant":
        item.grant_digest = "b" * 64
    requests = []

    async def handle(request):
        requests.append(request)
        assert request.method == "GET" and request.content == b""
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
        if case == "error":
            return httpx.Response(409, text="private upstream values")
        if case == "malformed":
            return httpx.Response(200, json={"private": "not a policy"})
        if request.url.path.endswith("/script-policy"):
            return httpx.Response(200, json=bound.model_dump(mode="json"))
        assert request.url.path == f"/v1/devices/{device}/script-catalog"
        scripts = [] if case == "missing_script" else [item.model_dump()]
        if case == "duplicate":
            scripts += scripts
        body = {"protocol_version": 1, "scripts": scripts}
        if case == "extra_catalog":
            body["command"] = "private forbidden command"
        return httpx.Response(200, json=body)

    original = httpx.AsyncClient

    def client(**options):
        assert (
            options["follow_redirects"] is False
            and options["trust_env"] is False
        )
        return original(**options, transport=httpx.MockTransport(handle))

    monkeypatch.setattr(module.httpx, "AsyncClient", client)
    if case == "valid":
        (
            policy,
            script,
        ) = await module.device_control_client.validate_script_configuration(
            project, device
        )
        assert (
            policy.device_id == device and script.script_id == "daily_report"
        )
        assert len(requests) == 2
    else:
        with pytest.raises(ExternalServiceError) as error:
            await module.device_control_client.validate_script_configuration(
                project, device
            )
        assert "private" not in str(error.value)
