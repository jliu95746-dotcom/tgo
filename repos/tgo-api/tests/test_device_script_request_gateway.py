"""Original preparation and stop outcomes cannot grant execution authority."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from app.api.company_configuration_access import require_configuration_write
from app.core.security import get_current_active_user

from app.schemas.agent_device_script_requests import (
    ScriptTrialControl, ScriptTrialRequestState,
)
from app.services.ai_client import AIServiceClient, ai_client
from tests.test_agent_device_script_trials import application, command, report

pytestmark = pytest.mark.asyncio


def state(project, actor, agent, request, ready=True):
    now = datetime.now(timezone.utc)
    return ScriptTrialRequestState(
        request_id=request.request_id, binding_id=request.binding_id,
        binding_revision=request.binding_revision,
        project_id=project, actor_id=actor, agent_id=agent,
        status="ready" if ready else "preparing",
        stop_status="finished" if ready else "none",
        created_at=now, deadline_at=now + timedelta(seconds=30),
        completed_at=now if ready else None, error_code=None,
        report=report(project, actor, agent, request) if ready else None,
    )


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("role", ["admin", "user"])
async def test_current_login_owns_request_status_and_cancel(
    monkeypatch, cancel, role
):
    app, actor = application(role)
    agent, request = uuid4(), command()
    value = state(actor.project_id, actor.id, agent, request, False)
    method = (
        "cancel_agent_device_script_request" if cancel
        else "agent_device_script_request_state"
    )
    operation = AsyncMock(return_value=value)
    monkeypatch.setattr(ai_client, method, operation)
    body = ScriptTrialControl.model_validate(request.model_dump(
        include=set(ScriptTrialControl.model_fields)
    ))
    suffix = "cancel" if cancel else "state"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.request(
            "POST" if cancel else "GET",
            f"/v1/ai/agents/{agent}/device-script-trials/requests/"
            f"{request.request_id}/{suffix}",
            params={"project_id": str(uuid4())},
            json=body.model_dump(mode="json") if cancel else None,
        )
    assert response.status_code == (200 if role == "admin" else 403)
    if role == "admin":
        assert operation.await_args.args == (
            actor.project_id, actor.id, agent,
            body if cancel else request.request_id,
        )
        assert response.json()["can_enable"] is False
    else:
        operation.assert_not_awaited()


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize(
    "fault", [None, "actor", "request", "binding", "nested_scope",
              "stop", "private_body", "missing_scope"]
)
async def test_proxy_requires_owned_complete_request_and_report(
    monkeypatch, cancel, fault
):
    project, actor, agent = uuid4(), uuid4(), uuid4()
    request = command()
    body = state(project, actor, agent, request).model_dump(mode="json")
    if fault == "actor":
        body["actor_id"] = str(uuid4())
    elif fault == "request":
        body["request_id"] = str(uuid4())
    elif fault == "binding":
        body["binding_id"] = str(uuid4())
    elif fault == "nested_scope":
        body["report"].pop("scope")
    elif fault == "stop":
        body["stop_status"] = "cancelled"
    elif fault == "private_body":
        body["parameters"] = {"PRIVATE": "fixture"}
    elif fault == "missing_scope":
        body.pop("scope")
    client = AIServiceClient()
    transport = AsyncMock(return_value=httpx.Response(200, json=body))
    monkeypatch.setattr(client, "_make_request", transport)
    control = ScriptTrialControl.model_validate(request.model_dump(
        include=set(ScriptTrialControl.model_fields)
    ))

    async def invoke():
        if cancel:
            return await client.cancel_agent_device_script_request(
                project, actor, agent, control
            )
        return await client.agent_device_script_request_state(
            project, actor, agent, request.request_id
        )

    if fault:
        with pytest.raises(HTTPException) as error:
            await invoke()
        assert error.value.status_code == 502
    else:
        assert (await invoke()).stop_status == "finished"
    call = transport.await_args
    assert call.kwargs["extra_headers"]["X-Staff-Id"] == str(actor)
    assert call.args[0] == ("POST" if cancel else "GET")
    assert len(transport.await_args_list) == 1
    if cancel:
        assert call.kwargs["json_data"] == control.model_dump(mode="json")
    else:
        assert "json_data" not in call.kwargs


async def test_path_mismatch_never_forwards_cancel(monkeypatch):
    app, _ = application()
    operation = AsyncMock()
    monkeypatch.setattr(
        ai_client, "cancel_agent_device_script_request", operation
    )
    request = command()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/v1/ai/agents/{uuid4()}/device-script-trials/requests/"
            f"{uuid4()}/cancel",
            json=request.model_dump(
                include=set(ScriptTrialControl.model_fields), mode="json"
            ),
        )
    assert response.status_code == 422
    operation.assert_not_awaited()


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("denial", ["session", "entitlement"])
async def test_request_control_keeps_session_and_configuration_guards(
    monkeypatch, cancel, denial
):
    app, _ = application()

    def denied():
        raise HTTPException(401 if denial == "session" else 403)

    dependency = (
        get_current_active_user if denial == "session"
        else require_configuration_write
    )
    app.dependency_overrides[dependency] = denied
    operation = AsyncMock()
    monkeypatch.setattr(ai_client, (
        "cancel_agent_device_script_request" if cancel
        else "agent_device_script_request_state"
    ), operation)
    request = command()
    suffix = "cancel" if cancel else "state"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.request(
            "POST" if cancel else "GET",
            f"/v1/ai/agents/{uuid4()}/device-script-trials/requests/"
            f"{request.request_id}/{suffix}",
            json=request.model_dump(
                include=set(ScriptTrialControl.model_fields), mode="json"
            ) if cancel else None,
        )
    assert response.status_code == (401 if denial == "session" else 403)
    operation.assert_not_awaited()
