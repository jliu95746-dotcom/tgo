"""Fail-closed device ownership checks at the service boundary."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest

from app.exceptions import ExternalServiceError, NotFoundError, ValidationError
from app.services import device_control_client as module
from app.schemas.device_session import DeviceExecutionIdentity


@pytest.mark.asyncio
@pytest.mark.parametrize("receipt_parent_matches", [True, False])
async def test_device_session_start_preserves_trusted_workflow_parent(
    monkeypatch, receipt_parent_matches,
):
    identity = DeviceExecutionIdentity(
        project_id=uuid4(), device_id=uuid4(), session_id=uuid4(),
        agent_id=uuid4(), agent_name="测试员工", workflow_execution_id=uuid4(),
    )
    original_client = httpx.AsyncClient

    async def handler(request):
        assert request.method == "POST"
        assert request.url.path.endswith(f"/{identity.session_id}/start")
        import json
        body = json.loads(request.content)
        assert body["workflow_execution_id"] == str(identity.workflow_execution_id)
        return httpx.Response(200, json={
            "id": str(identity.session_id), "device_id": str(identity.device_id),
            "agent_id": str(identity.agent_id), "status": "running",
            "workflow_execution_id": str(
                identity.workflow_execution_id if receipt_parent_matches else uuid4()
            ),
            "lease_expires_at": (
                datetime.now(timezone.utc) + timedelta(minutes=1)
            ).isoformat(),
        })

    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: original_client(
        **kwargs, transport=httpx.MockTransport(handler),
    ))
    if receipt_parent_matches:
        await module.device_control_client.start_session(identity)
    else:
        with pytest.raises(ExternalServiceError):
            await module.device_control_client.start_session(identity)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "foreign",
        "forbidden",
        "mismatch",
        "invalid_json",
        "invalid_schema",
        "redirect",
        "timeout",
    ],
)
async def test_binding_validation_http_boundary(monkeypatch, case):
    project, device = uuid4(), uuid4()
    requests = []
    original_client = httpx.AsyncClient

    async def handler(request):
        requests.append(request)
        if request.url.path == f"/v1/devices/{device}/read-policy":
            assert case == "valid"
            return httpx.Response(404, json={"detail": "Device read policy not found"})
        assert request.url.path == f"/v1/devices/{device}"
        assert request.headers["Authorization"].startswith("Bearer ")
        if case == "timeout":
            raise httpx.ReadTimeout("fixture timeout", request=request)
        if case in ("foreign", "forbidden"):
            return httpx.Response(404 if case == "foreign" else 403)
        if case == "redirect":
            return httpx.Response(
                302, headers={"Location": "https://untrusted.invalid"}
            )
        if case == "invalid_json":
            return httpx.Response(200, text="not-json")
        if case == "invalid_schema":
            return httpx.Response(200, json={"id": str(device)})
        return httpx.Response(
            200,
            json={
                "id": str(device),
                "project_id": str(uuid4() if case == "mismatch" else project),
            },
        )

    def client(**kwargs):
        assert kwargs["follow_redirects"] is False
        return original_client(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(module.httpx, "AsyncClient", client)
    if case == "valid":
        assert await module.device_control_client.validate_binding(project, str(device)) is None
    else:
        error = (
            NotFoundError
            if case in ("foreign", "forbidden", "mismatch")
            else ExternalServiceError
        )
        with pytest.raises(error):
            await module.device_control_client.validate_binding(project, str(device))
    assert len(requests) == (2 if case == "valid" else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "policy_status,verified,accepted",
    [("active", True, True), ("active", False, False), ("pending", False, False), ("revoked", True, False)],
)
async def test_published_read_policy_requires_current_test(
    monkeypatch, policy_status, verified, accepted
):
    project, device = uuid4(), uuid4()
    requests = []
    original_client = httpx.AsyncClient

    async def handler(request):
        requests.append(request.url.path)
        if request.url.path == f"/v1/devices/{device}":
            return httpx.Response(
                200, json={"id": str(device), "project_id": str(project)}
            )
        assert request.url.path == f"/v1/devices/{device}/read-policy"
        return httpx.Response(
            200,
            json={
                "device_id": str(device),
                "project_id": str(project),
                "tool_name": "fs_read",
                "status": policy_status,
                "verified": verified,
                "digest": "a" * 64,
            },
        )

    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            **kwargs, transport=httpx.MockTransport(handler)
        ),
    )
    if accepted:
        policy = await module.device_control_client.validate_binding(project, str(device))
        assert policy is not None and policy.digest == "a" * 64
    else:
        with pytest.raises(ValidationError):
            await module.device_control_client.validate_binding(project, str(device))
    assert requests == [
        f"/v1/devices/{device}",
        f"/v1/devices/{device}/read-policy",
    ]


@pytest.mark.asyncio
async def test_foreign_read_policy_cannot_authorize_binding(monkeypatch):
    project, device = uuid4(), uuid4()
    original_client = httpx.AsyncClient

    async def handler(request):
        if request.url.path == f"/v1/devices/{device}":
            return httpx.Response(
                200, json={"id": str(device), "project_id": str(project)}
            )
        return httpx.Response(
            200,
            json={
                "device_id": str(device),
                "project_id": str(uuid4()),
                "tool_name": "fs_read",
                "status": "active",
                "verified": True,
                    "digest": "a" * 64,
            },
        )

    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            **kwargs, transport=httpx.MockTransport(handler)
        ),
    )
    with pytest.raises(NotFoundError):
        await module.device_control_client.validate_binding(project, str(device))


@pytest.mark.asyncio
async def test_unknown_policy_404_does_not_bypass_binding_gate(monkeypatch):
    project, device = uuid4(), uuid4()
    original_client = httpx.AsyncClient

    async def handler(request):
        if request.url.path == f"/v1/devices/{device}":
            return httpx.Response(
                200, json={"id": str(device), "project_id": str(project)}
            )
        return httpx.Response(404, json={"detail": "Not Found"})

    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            **kwargs, transport=httpx.MockTransport(handler)
        ),
    )
    with pytest.raises(NotFoundError):
        await module.device_control_client.validate_binding(project, str(device))


@pytest.mark.asyncio
async def test_invalid_device_identifier_never_opens_connection(monkeypatch):
    def unexpected(**kwargs):
        pytest.fail("Invalid device identifier must be rejected before network access")

    monkeypatch.setattr(module.httpx, "AsyncClient", unexpected)
    with pytest.raises(ValidationError):
        await module.device_control_client.validate_binding(uuid4(), "../foreign")
