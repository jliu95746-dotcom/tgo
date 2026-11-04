"""Business run transport preserves identity and suppresses private errors."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import agent_device_script_runs
from app.schemas.agent_device_script_runs import (
    ScriptRunStart,
    ScriptRunView,
)
from app.services.ai_client import AIServiceClient, ai_client
from tests.test_agent_device_script_trials import application


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "user"])
@pytest.mark.parametrize("operation", ["start", "read", "cancel"])
async def test_run_routes_preserve_owner_and_original_request(
    monkeypatch,
    role,
    operation,
):
    app, actor = application(role)
    app.include_router(
        agent_device_script_runs.router, prefix="/v1/ai/agents"
    )
    agent, now = uuid4(), datetime.now(timezone.utc)
    command = ScriptRunStart(
        request_id=uuid4(),
        report_id=uuid4(),
        review_id=uuid4(),
        parameters={"label": "private-business-input"},
    )
    value = ScriptRunView(
        **command.model_dump(exclude={"parameters"}),
        id=uuid4(),
        project_id=actor.project_id,
        actor_id=actor.id,
        agent_id=agent,
        execution_id=None,
        status="preparing",
        error_code=None,
        created_at=now,
        deadline_at=now + timedelta(seconds=60),
        dispatch_started_at=None,
        completed_at=None,
        stop_requested_at=None,
        result_digest=None,
        result_fields=None,
    )
    name = {
        "start": "start_agent_device_script_run",
        "read": "agent_device_script_run",
        "cancel": "cancel_agent_device_script_run",
    }[operation]
    action = AsyncMock(return_value=value)
    monkeypatch.setattr(ai_client, name, action)
    path = f"/v1/ai/agents/{agent}/device-script-runs"
    body = command.model_dump(mode="json")
    if operation != "start":
        path += f"/requests/{command.request_id}"
    if operation == "cancel":
        path += "/cancel"
        body.pop("parameters")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://test",
    ) as client:
        response = await client.request(
            "GET" if operation == "read" else "POST",
            path,
            json=None if operation == "read" else body,
        )
    assert response.status_code == (200 if role == "admin" else 403)
    if role != "admin":
        action.assert_not_awaited()
        return
    assert response.headers["cache-control"] == "no-store"
    assert action.await_args.args[:3] == (
        actor.project_id,
        actor.id,
        agent,
    )
    proxy = AIServiceClient()
    forward = AsyncMock(
        return_value=httpx.Response(
            200,
            json=value.model_dump(mode="json"),
        )
    )
    monkeypatch.setattr(proxy, "_make_request", forward)
    args = (
        actor.project_id,
        actor.id,
        agent,
        command.request_id
        if operation == "read"
        else command
        if operation == "start"
        else command.model_copy().model_dump(exclude={"parameters"}),
    )
    if operation == "cancel":
        from app.schemas.agent_device_script_runs import (
            ScriptRunControl,
        )

        args = (*args[:3], ScriptRunControl.model_validate(args[3]))
    assert await getattr(proxy, name)(*args) == value
    for field in ("project_id", "actor_id", "agent_id", "request_id"):
        forward.return_value = httpx.Response(
            200,
            json=value.model_copy(
                update={field: uuid4()},
            ).model_dump(mode="json"),
        )
        with pytest.raises(HTTPException) as error:
            await getattr(proxy, name)(*args)
        assert error.value.status_code == 502
    if operation == "read":
        malformed = value.model_copy(update={
            "status": "succeeded", "execution_id": uuid4(),
            "dispatch_started_at": now, "completed_at": now,
            "result_digest": "a" * 64,
            "result_fields": {"summary": "x" * 65537},
        })
        forward.return_value = httpx.Response(
            200, json=malformed.model_dump(mode="json"),
        )
        with pytest.raises(HTTPException) as too_large:
            await getattr(proxy, name)(*args)
        assert too_large.value.status_code == 502


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["value", "key", "extra"])
async def test_run_validation_keeps_input_and_field_names_private(
    monkeypatch,
    caplog,
    where,
):
    app, _ = application()
    app.include_router(
        agent_device_script_runs.router, prefix="/v1/ai/agents"
    )
    marker = "PRIVATE-BUSINESS-INPUT-MARKER"
    body = {
        "request_id": str(uuid4()),
        "report_id": str(uuid4()),
        "review_id": str(uuid4()),
        "parameters": {"label": "sample"},
    }
    if where == "value":
        body["parameters"] = {"label": {"value": marker}}
    elif where == "key":
        body["parameters"] = {marker: "value"}
    else:
        body[marker] = "value"
    action = AsyncMock()
    monkeypatch.setattr(
        ai_client, "start_agent_device_script_run", action
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            f"/v1/ai/agents/{uuid4()}/device-script-runs",
            json=body,
        )
    assert response.status_code == 422
    assert marker not in response.text and marker not in caplog.text
    action.assert_not_awaited()
