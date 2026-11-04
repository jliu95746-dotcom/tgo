"""A logged-in administrator can test a fixed binding without approval."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError

from app.api.company_configuration_access import require_configuration_write
from app.api.v1.endpoints.agent_device_script_trials import router
from app.core.exceptions import validation_exception_handler
from app.core.security import get_current_active_user
from app.schemas.agent_device_script_trials import (
    ScriptTrialReport,
    ScriptTrialStart,
)
from app.services.ai_client import AIServiceClient, ai_client

pytestmark = pytest.mark.asyncio


def command():
    return ScriptTrialStart(
        request_id=uuid4(), binding_id=uuid4(), binding_revision=3,
        parameters={"label": "private-fixture-label"},
        expected_fields={"ready": True, "count": 3},
    )


def report(project, actor, employee, request):
    now = datetime.now(timezone.utc)
    return ScriptTrialReport(
        id=uuid4(), project_id=project, actor_id=actor, agent_id=employee,
        request_id=request.request_id, device_id=uuid4(), execution_id=uuid4(),
        binding_id=request.binding_id,
        binding_revision=request.binding_revision,
        policy_digest="a" * 64, grant_digest="b" * 64,
        expected_digest="c" * 64, result_digest="c" * 64,
        status="matched", error_code=None, remaining_runs=0,
        applicability="current", created_at=now, completed_at=now,
        expires_at=now + timedelta(days=7), dispatch_claimed_at=now,
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


@pytest.mark.parametrize("kind", ["start", "read", "recover"])
async def test_routes_use_only_logged_in_identity(monkeypatch, kind):
    app, actor = application()
    employee, request = uuid4(), command()
    value = report(actor.project_id, actor.id, employee, request)
    method = {
        "start": "start_agent_device_script_trial",
        "read": "get_agent_device_script_trial",
        "recover": "agent_device_script_trial_request",
    }[kind]
    operation = AsyncMock(return_value=value)
    monkeypatch.setattr(ai_client, method, operation)
    suffix = {
        "start": "", "read": f"/{value.id}",
        "recover": f"/requests/{request.request_id}",
    }[kind]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.request(
            "POST" if kind == "start" else "GET",
            f"/v1/ai/agents/{employee}/device-script-trials{suffix}",
            params={"project_id": str(uuid4()), "actor_id": str(uuid4())},
            headers={"X-Staff-Id": str(uuid4())},
            json=request.model_dump(mode="json") if kind == "start" else None,
        )
    assert response.status_code == 200
    assert response.json()["assessment"] == "not_reviewed"
    assert response.json()["can_enable"] is False
    assert "private-fixture-label" not in response.text
    expected = (
        request if kind == "start" else
        value.id if kind == "read" else request.request_id
    )
    assert operation.await_args.args == (
        actor.project_id, actor.id, employee, expected
    )


@pytest.mark.parametrize("kind", ["start", "read", "recover"])
@pytest.mark.parametrize("denial", ["role", "entitlement", "unauthenticated"])
async def test_all_routes_reject_unauthorized_callers(
    monkeypatch, kind, denial
):
    app, _ = application("user" if denial == "role" else "admin")

    def denied():
        raise HTTPException(401 if denial == "unauthenticated" else 403)

    if denial != "role":
        dependency = (
            get_current_active_user if denial == "unauthenticated"
            else require_configuration_write
        )
        app.dependency_overrides[dependency] = denied
    operation = AsyncMock()
    method = {
        "start": "start_agent_device_script_trial",
        "read": "get_agent_device_script_trial",
        "recover": "agent_device_script_trial_request",
    }[kind]
    monkeypatch.setattr(ai_client, method, operation)
    suffix = "" if kind == "start" else (
        f"/{uuid4()}" if kind == "read" else f"/requests/{uuid4()}"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.request(
            "POST" if kind == "start" else "GET",
            f"/v1/ai/agents/{uuid4()}/device-script-trials{suffix}",
            json=(
                command().model_dump(mode="json") if kind == "start" else None
            ),
        )
    expected_status = 401 if denial == "unauthenticated" else 403
    assert response.status_code == expected_status
    operation.assert_not_awaited()


@pytest.mark.parametrize(
    "bad_input",
    [
        {"binding_revision": True},
        {"parameters": {"label": {"private-fixture-input": "nested"}}},
        {"expected_fields": {"count": 1.5}},
        {"expected_fields": {}},
        {"parameters": {"label": "private-fixture-input" * 12000}},
        {"project_id": "private-fixture-input"},
        {"parameters": {"private-fixture-input": True}},
        {"expected_fields": {"private-fixture-input": {"nested": True}}},
        {"private-fixture-input": "extra"},
    ],
)
async def test_invalid_private_inputs_are_redacted(monkeypatch, bad_input):
    from app.core import exceptions

    app, _ = application()
    operation = AsyncMock()
    monkeypatch.setattr(
        ai_client, "start_agent_device_script_trial", operation
    )
    warning = Mock()
    monkeypatch.setattr(exceptions.logger, "warning", warning)
    body = command().model_dump(mode="json") | bad_input
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/v1/ai/agents/{uuid4()}/device-script-trials", json=body
        )
    assert response.status_code == 422
    assert "private-fixture" not in response.text
    assert "private-fixture" not in repr(warning.call_args_list)
    operation.assert_not_awaited()


@pytest.mark.parametrize("kind", ["start", "read", "recover"])
@pytest.mark.parametrize(
    "fault", [None, "project_id", "actor_id", "agent_id", "request_id",
              "id", "binding_id", "binding_revision", "can_enable",
              "missing_scope", "missing_assessment", "private_body",
              "comparison"]
)
async def test_proxy_rejects_wrong_or_incomplete_receipts(
    monkeypatch, kind, fault
):
    project, actor, employee = uuid4(), uuid4(), uuid4()
    request = command()
    value = report(project, actor, employee, request)
    body = value.model_dump(mode="json")
    relevant = fault not in {"id", "binding_id", "binding_revision"} or (
        fault == "id" and kind == "read"
        or fault in {"binding_id", "binding_revision"} and kind == "start"
    )
    if fault in {"project_id", "actor_id", "agent_id", "request_id", "id",
                 "binding_id"}:
        body[fault] = str(uuid4())
    elif fault == "binding_revision":
        body[fault] += 1
    elif fault == "can_enable":
        body[fault] = True
    elif fault in {"missing_scope", "missing_assessment"}:
        body.pop(fault.removeprefix("missing_"))
    elif fault == "private_body":
        body["fields"] = {"label": "private-fixture-result"}
    elif fault == "comparison":
        body["result_digest"] = "d" * 64
    # A report lookup binds its report ID, not an unknown request ID.
    if fault == "request_id" and kind == "read":
        relevant = False
    transport = AsyncMock(return_value=httpx.Response(200, json=body))
    client = AIServiceClient()
    monkeypatch.setattr(client, "_make_request", transport)

    async def invoke():
        if kind == "start":
            return await client.start_agent_device_script_trial(
                project, actor, employee, request
            )
        if kind == "read":
            return await client.get_agent_device_script_trial(
                project, actor, employee, value.id
            )
        return await client.agent_device_script_trial_request(
            project, actor, employee, request.request_id
        )

    if fault and relevant:
        with pytest.raises(HTTPException) as error:
            await invoke()
        assert error.value.status_code == 502
        assert "private-fixture" not in str(error.value)
    else:
        result = await invoke()
        assert result.can_enable is False
    call = transport.await_args
    assert call.kwargs["extra_headers"]["X-Project-Id"] == str(project)
    assert call.kwargs["extra_headers"]["X-Staff-Id"] == str(actor)
    assert call.kwargs["extra_headers"]["X-Internal-API-Key"]
    assert len(transport.await_args_list) == 1
    if kind == "start":
        assert call.args == (
            "POST", f"/api/v1/agents/{employee}/device-script-trials"
        )
        assert call.kwargs["json_data"] == request.model_dump(mode="json")
        assert call.kwargs["timeout"] >= 150
    else:
        assert call.args[0] == "GET"
        assert "json_data" not in call.kwargs
        suffix = f"/{value.id}" if kind == "read" else (
            f"/requests/{request.request_id}"
        )
        assert call.args[1].endswith(suffix)


@pytest.mark.parametrize("status", [403, 404, 409, 422, 503])
async def test_upstream_errors_never_expose_input_or_result(
    monkeypatch, status
):
    client = AIServiceClient()
    transport = AsyncMock(
        return_value=httpx.Response(status, text="private-fixture-upstream")
    )
    monkeypatch.setattr(client, "_make_request", transport)
    with pytest.raises(HTTPException) as error:
        await client.start_agent_device_script_trial(
            uuid4(), uuid4(), uuid4(), command()
        )
    assert error.value.status_code == status
    assert "private-fixture" not in str(error.value)
    assert transport.await_count == 1


async def test_actual_transport_does_not_retry_a_timed_out_post(monkeypatch):
    real_client = httpx.AsyncClient
    requests = []

    async def timeout(request):
        requests.append(request)
        raise httpx.ReadTimeout("fixture timeout", request=request)

    def isolated_client(**kwargs):
        return real_client(transport=httpx.MockTransport(timeout), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", isolated_client)
    client = AIServiceClient()
    client.base_url = "http://owned-fixture.invalid"
    with pytest.raises(HTTPException) as error:
        await client.start_agent_device_script_trial(
            uuid4(), uuid4(), uuid4(), command()
        )
    assert error.value.status_code == 504
    assert len(requests) == 1
    assert requests[0].method == "POST"
