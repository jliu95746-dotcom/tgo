"""Script management validates admin scope and bounded upstream metadata."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from jose import jwt

from app.api.v1.endpoints import device_script_policies
from app.core.config import settings
from app.core.exceptions import (
    ExternalServiceError,
    TGOAPIException,
    tgo_api_exception_handler,
)
from app.core.security import get_current_active_user
from app.schemas.device_script_policy import ScriptPolicy, script_policy_digest
from app.schemas.device_script_publication import (
    ScriptPolicyPublish,
    ScriptPolicyRevoke,
)
from app.services.device_control_client import DeviceControlClient


def publish_body():
    return ScriptPolicyPublish(
        expected_policy_revision=0,
        connection_instance_id="c" * 32,
        session_id="d" * 32,
        script_id="daily_report",
        grant_digest="a" * 64,
        timeout_seconds=10,
        max_output_bytes=4096,
        max_runs=1,
    )


def policy_result(project, device, **changes):
    policy = ScriptPolicy.model_construct(
        protocol_version=1,
        revision=1,
        script_id="daily_report",
        grant_digest="a" * 64,
        timeout_seconds=10,
        max_output_bytes=4096,
        max_runs=1,
        digest="",
    )
    policy.digest = script_policy_digest(policy)
    return dict(
        device_id=str(device),
        project_id=str(project),
        policy=policy.model_dump(),
        status="active",
        verified=False,
        updated_at=datetime.now(timezone.utc).isoformat(),
        **changes,
    )


@pytest.mark.asyncio
async def test_management_client_signs_device_and_never_submits_code(
    monkeypatch,
):
    project, device = uuid4(), uuid4()
    observed = []

    def handle(request):
        observed.append(request)
        return httpx.Response(200, json=policy_result(project, device))

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **options: original(
            transport=httpx.MockTransport(handle), **options
        ),
    )
    result = await DeviceControlClient().publish_script_policy(
        device, str(project), publish_body()
    )
    assert result.policy.revision == 1 and result.verified is False
    sent = observed[0]
    assert sent.method == "PUT"
    assert sent.url.path == f"/v1/devices/{device}/script-policy"
    claims = jwt.decode(
        sent.headers["Authorization"][7:],
        settings.SECRET_KEY,
        algorithms=["HS256"],
        audience="tgo-device-control",
        issuer="tgo-internal",
    )
    assert claims["sub"] == "tgo-api"
    assert claims["project_id"] == str(project)
    assert claims["device_id"] == str(device)
    assert sent.url.query == b""
    import json

    assert json.loads(sent.content) == publish_body().model_dump()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed", ["project", "device", "digest", "extra", "verified"]
)
async def test_client_rejects_foreign_or_invalid_response(
    monkeypatch, changed
):
    project, device = uuid4(), uuid4()
    response = policy_result(project, device)
    if changed == "project":
        response["project_id"] = str(uuid4())
    elif changed == "device":
        response["device_id"] = str(uuid4())
    elif changed == "digest":
        response["policy"]["digest"] = "b" * 64
    elif changed == "extra":
        response["script_path"] = "private"
    else:
        response["verified"] = True
    client = DeviceControlClient()
    monkeypatch.setattr(client, "_request", AsyncMock(return_value=response))
    with pytest.raises(ExternalServiceError):
        await client.get_script_policy(device, str(project))


@pytest.mark.asyncio
async def test_publish_and_revoke_must_match_the_requested_revision(
    monkeypatch,
):
    project, device = uuid4(), uuid4()
    response = policy_result(project, device)
    policy = ScriptPolicy.model_validate(response["policy"])
    policy.max_runs = 2
    policy.digest = script_policy_digest(policy)
    response["policy"] = policy.model_dump()
    client = DeviceControlClient()
    monkeypatch.setattr(client, "_request", AsyncMock(return_value=response))
    with pytest.raises(ExternalServiceError):
        await client.publish_script_policy(
            device, str(project), publish_body()
        )
    with pytest.raises(ExternalServiceError):
        await client.revoke_script_policy(
            device,
            str(project),
            ScriptPolicyRevoke(expected_policy_revision=1),
        )


@pytest.mark.asyncio
async def test_non_admin_cannot_discover_publish_or_revoke(monkeypatch):
    app = FastAPI()
    app.include_router(device_script_policies.router)
    app.dependency_overrides[
        get_current_active_user
    ] = lambda: SimpleNamespace(role="agent", project_id=uuid4())
    send = AsyncMock()
    monkeypatch.setattr(
        device_script_policies.device_control_client, "_request", send
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for method, suffix, body in (
            ("GET", "script-status", None),
            ("GET", "script-catalog", None),
            ("GET", "script-policy", None),
            ("PUT", "script-policy", publish_body().model_dump()),
            ("POST", "script-policy/revoke", {"expected_policy_revision": 1}),
        ):
            response = await client.request(
                method, f"/devices/{uuid4()}/{suffix}", json=body
            )
            assert response.status_code == 403
    send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 404, 409, 422, 500])
async def test_gateway_preserves_safe_status_without_private_error(
    monkeypatch, status
):
    project, device = uuid4(), uuid4()
    app = FastAPI()
    app.add_exception_handler(TGOAPIException, tgo_api_exception_handler)
    app.include_router(device_script_policies.router)
    app.dependency_overrides[
        get_current_active_user
    ] = lambda: SimpleNamespace(role="admin", project_id=project)
    upstream = httpx.Request("PUT", "http://fixture.invalid/script-policy")
    error = httpx.HTTPStatusError(
        "private-upstream-marker",
        request=upstream,
        response=httpx.Response(
            status,
            json={"detail": "private-upstream-marker"},
            request=upstream,
        ),
    )
    send = AsyncMock(side_effect=error)
    monkeypatch.setattr(
        device_script_policies.device_control_client, "_request", send
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.put(
            f"/devices/{device}/script-policy?project_id={uuid4()}",
            json=publish_body().model_dump(),
        )
    assert response.status_code == (502 if status == 500 else status)
    assert "private-upstream-marker" not in response.text
    assert send.await_args.args[2] == str(project)


def test_management_contract_matches_the_frozen_policy_vector():
    policy = ScriptPolicy.model_construct(
        protocol_version=1,
        revision=3,
        script_id="daily_report",
        grant_digest="a" * 64,
        timeout_seconds=20,
        max_output_bytes=4096,
        max_runs=2,
        digest="",
    )
    assert script_policy_digest(policy) == (
        "c67ba11a5189a797b80e7e00fc943219c" "91bd270aba8929db2227b4d388fe057"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"command": "not-accepted"},
        {"script_path": "not-accepted"},
        {"project_id": "not-accepted"},
        {"verified": True},
        {"max_runs": True},
        {"connection_instance_id": "stale"},
    ],
)
async def test_invalid_publication_never_reaches_the_device(
    monkeypatch, change
):
    project, device = uuid4(), uuid4()
    app = FastAPI()
    app.include_router(device_script_policies.router)
    app.dependency_overrides[
        get_current_active_user
    ] = lambda: SimpleNamespace(role="admin", project_id=project)
    send = AsyncMock()
    monkeypatch.setattr(
        device_script_policies.device_control_client, "_request", send
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.put(
            f"/devices/{device}/script-policy",
            json={**publish_body().model_dump(), **change},
        )
    assert response.status_code == 422
    send.assert_not_awaited()
