"""Admin scope, original revisions and non-authorizing script config DTOs."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError

from app.api.company_configuration_access import require_configuration_write
from app.api.v1.endpoints.agent_device_script_bindings import router
from app.core.exceptions import validation_exception_handler
from app.core.security import get_current_active_user
from app.schemas.agent_device_script_binding import (
    AgentDeviceScriptRecord,
    AgentDeviceScriptState,
    AgentDeviceScriptWrite,
)
from app.services.ai_client import AIServiceClient, ai_client

pytestmark = pytest.mark.asyncio


def record(project, actor, employee, device):
    return AgentDeviceScriptRecord(
        id=uuid4(),
        project_id=project,
        agent_id=employee,
        device_id=device,
        script_id="daily_report",
        policy_digest="a" * 64,
        grant_digest="b" * 64,
        revision=1,
        enabled=True,
        configured_by=actor,
    )


def application(role="admin"):
    actor = SimpleNamespace(id=uuid4(), project_id=uuid4(), role=role)
    app = FastAPI()
    app.include_router(router, prefix="/v1/ai/agents")
    app.add_exception_handler(
        RequestValidationError, validation_exception_handler
    )
    app.dependency_overrides[get_current_active_user] = lambda: actor
    app.dependency_overrides[require_configuration_write] = lambda: None
    return app, actor


async def test_admin_write_ignores_client_supplied_identity(monkeypatch):
    app, actor = application()
    employee, device = uuid4(), uuid4()
    value = record(actor.project_id, actor.id, employee, device)
    operation = AsyncMock(return_value=value)
    monkeypatch.setattr(
        ai_client, "save_agent_device_script_binding", operation
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.put(
            f"/v1/ai/agents/{employee}/device-script-binding",
            params={"project_id": str(uuid4())},
            headers={"X-Staff-Id": str(uuid4())},
            json={
                "request_id": str(uuid4()),
                "expected_revision": 0,
                "device_id": str(device),
                "expected_policy_digest": "a" * 64,
            },
        )
    assert response.status_code == 200
    assert operation.await_args.args[:3] == (
        actor.project_id,
        actor.id,
        employee,
    )
    assert response.json()["can_enable"] is False


@pytest.mark.parametrize(
    "method,suffix,body",
    [
        ("GET", "", None),
        ("GET", "/requests/" + str(uuid4()), None),
        (
            "PUT",
            "",
            {
                "request_id": str(uuid4()),
                "expected_revision": 0,
                "device_id": str(uuid4()),
                "expected_policy_digest": "a" * 64,
            },
        ),
    ],
)
async def test_non_admin_cannot_use_binding_routes(method, suffix, body):
    app, _ = application("user")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.request(
            method,
            f"/v1/ai/agents/{uuid4()}/device-script-binding{suffix}",
            json=body,
        )
    assert response.status_code == 403


async def test_rejected_configuration_does_not_echo_private_value(monkeypatch):
    from app.core import exceptions

    warning = Mock()
    monkeypatch.setattr(exceptions.logger, "warning", warning)
    app, _ = application()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.put(
            f"/v1/ai/agents/{uuid4()}/device-script-binding",
            json={
                "request_id": str(uuid4()),
                "expected_revision": 0,
                "device_id": str(uuid4()),
                "expected_policy_digest": "a" * 64,
                "secret": "private-synthetic-input",
            },
        )
    assert response.status_code == 422
    assert "private-synthetic-input" not in response.text
    assert warning.called
    assert "private-synthetic-input" not in repr(warning.call_args_list)


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "project",
        "agent",
        "actor",
        "device",
        "revision",
        "policy",
        "approval",
        "missing_scope",
    ],
)
async def test_proxy_checks_scope_revision_and_non_approval(monkeypatch, case):
    project, actor, employee, device = (uuid4() for _ in range(4))
    command = AgentDeviceScriptWrite(
        request_id=uuid4(),
        expected_revision=0,
        device_id=device,
        expected_policy_digest="a" * 64,
    )
    body = record(project, actor, employee, device).model_dump(mode="json")
    key = {
        "project": "project_id",
        "agent": "agent_id",
        "actor": "configured_by",
        "device": "device_id",
    }.get(case)
    if key:
        body[key] = str(uuid4())
    elif case == "revision":
        body["revision"] = 4
    elif case == "policy":
        body["policy_digest"] = "c" * 64
    elif case == "approval":
        body["can_enable"] = True
    elif case == "missing_scope":
        body.pop("scope")
    client = AIServiceClient()
    transport = AsyncMock(return_value=httpx.Response(200, json=body))
    monkeypatch.setattr(client, "_make_request", transport)
    if case == "valid":
        result = await client.save_agent_device_script_binding(
            project, actor, employee, command
        )
        assert result.device_id == device and not result.can_enable
        call = transport.await_args
        assert call.args == (
            "PUT",
            f"/api/v1/agents/{employee}/device-script-binding",
        )
        assert call.kwargs["extra_headers"]["X-Project-Id"] == str(project)
        assert call.kwargs["extra_headers"]["X-Staff-Id"] == str(actor)
        assert call.kwargs["json_data"] == command.model_dump(mode="json")
    else:
        with pytest.raises(HTTPException) as error:
            await client.save_agent_device_script_binding(
                project, actor, employee, command
            )
        assert error.value.status_code == 502


@pytest.mark.parametrize("status", [403, 409, 422, 503])
async def test_upstream_error_body_is_not_forwarded(monkeypatch, status):
    client = AIServiceClient()
    monkeypatch.setattr(
        client,
        "_make_request",
        AsyncMock(
            return_value=httpx.Response(status, text="private upstream input")
        ),
    )
    with pytest.raises(HTTPException) as error:
        await client.agent_device_script_binding(uuid4(), uuid4(), uuid4())
    assert error.value.status_code == status
    assert "private" not in str(error.value)


@pytest.mark.parametrize("kind", ["read", "recover"])
@pytest.mark.parametrize("fault", [False, True])
async def test_proxy_read_and_recovery_keep_owner_scope(
    monkeypatch, kind, fault
):
    project, actor, employee, device, request = (uuid4() for _ in range(5))
    row = record(project, actor, employee, device).model_dump(mode="json")
    if fault:
        row["agent_id" if kind == "read" else "configured_by"] = str(uuid4())
    body = (
        {"project_id": str(project), "agent_id": str(employee), "binding": row}
        if kind == "read"
        else row
    )
    client = AIServiceClient()
    transport = AsyncMock(return_value=httpx.Response(200, json=body))
    monkeypatch.setattr(client, "_make_request", transport)

    async def invoke():
        if kind == "read":
            return await client.agent_device_script_binding(
                project, actor, employee
            )
        return await client.agent_device_script_binding_request(
            project, actor, employee, request
        )

    if fault:
        with pytest.raises(HTTPException) as error:
            await invoke()
        assert error.value.status_code == 502
    else:
        await invoke()
        suffix = "" if kind == "read" else f"/requests/{request}"
        assert transport.await_args.args == (
            "GET",
            f"/api/v1/agents/{employee}/device-script-binding{suffix}",
        )
        assert transport.await_args.kwargs["extra_headers"][
            "X-Staff-Id"
        ] == str(actor)


@pytest.mark.parametrize("recover", [False, True])
async def test_admin_reads_and_recovers_without_client_identity_override(
    monkeypatch, recover
):
    app, actor = application()
    employee, device, request = uuid4(), uuid4(), uuid4()
    row = record(actor.project_id, actor.id, employee, device)
    result = (
        row
        if recover
        else AgentDeviceScriptState(
            project_id=actor.project_id, agent_id=employee, binding=row
        )
    )
    operation = AsyncMock(return_value=result)
    method = (
        "agent_device_script_binding_request"
        if recover
        else "agent_device_script_binding"
    )
    monkeypatch.setattr(ai_client, method, operation)
    suffix = f"/requests/{request}" if recover else ""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.get(
            f"/v1/ai/agents/{employee}/device-script-binding{suffix}",
            params={"project_id": str(uuid4())},
        )
    assert response.status_code == 200
    expected = (
        (actor.project_id, actor.id, employee, request)
        if recover
        else (actor.project_id, actor.id, employee)
    )
    assert operation.await_args.args == expected


async def test_configuration_entitlement_guard_is_kept(monkeypatch):
    app, _ = application()

    def denied():
        raise HTTPException(403, "当前配置权限不可用")

    app.dependency_overrides[require_configuration_write] = denied
    operation = AsyncMock()
    monkeypatch.setattr(
        ai_client, "save_agent_device_script_binding", operation
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.put(
            f"/v1/ai/agents/{uuid4()}/device-script-binding",
            json={
                "request_id": str(uuid4()),
                "expected_revision": 0,
                "device_id": str(uuid4()),
                "expected_policy_digest": "a" * 64,
            },
        )
    assert response.status_code == 403
    operation.assert_not_awaited()
