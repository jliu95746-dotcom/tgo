"""Selection endpoints cannot invent ownership or saved versions."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import agent_device_script_selection as module
from app.schemas.agent_device_script_selection import (
    ScriptSelectionRecord,
    ScriptSelectionState,
    ScriptSelectionWrite,
)
from app.services.ai_client import AIServiceClient, ai_client
from tests.test_agent_device_script_trials import application


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "user"])
@pytest.mark.parametrize("operation", ["save", "read", "original"])
async def test_selection_routes_and_client_preserve_scope(
    monkeypatch,
    role,
    operation,
):
    app, actor = application(role)
    app.include_router(module.router, prefix="/v1/ai/agents")
    agent, now = uuid4(), datetime.now(timezone.utc)
    command = ScriptSelectionWrite(
        request_id=uuid4(),
        expected_revision=0,
        report_id=uuid4(),
        review_id=uuid4(),
    )
    record = ScriptSelectionRecord(
        id=uuid4(),
        project_id=actor.project_id,
        agent_id=agent,
        request_id=command.request_id,
        configured_by=actor.id,
        revision=1,
        enabled=True,
        binding_id=uuid4(),
        binding_revision=1,
        report_id=command.report_id,
        review_id=command.review_id,
        saved_at=now,
        expires_at=now + timedelta(hours=1),
        selection_digest="a" * 64,
    )
    value = (
        ScriptSelectionState(
            project_id=actor.project_id,
            actor_id=actor.id,
            agent_id=agent,
            selection=record,
            status="selected",
        )
        if operation == "read"
        else record
    )
    name = {
        "save": "save_agent_device_script_selection",
        "read": "agent_device_script_selection",
        "original": "agent_device_script_selection_request",
    }[operation]
    action = AsyncMock(return_value=value)
    monkeypatch.setattr(ai_client, name, action)
    path = f"/v1/ai/agents/{agent}/device-script-selection"
    if operation == "original":
        path += f"/requests/{command.request_id}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://test",
    ) as client:
        response = await client.request(
            "POST" if operation == "save" else "GET",
            path,
            json=command.model_dump(mode="json")
            if operation == "save"
            else None,
        )
    assert response.status_code == (200 if role == "admin" else 403)
    if role != "admin":
        action.assert_not_awaited()
        return
    assert action.await_args.args[:3] == (actor.project_id, actor.id, agent)
    assert response.headers["cache-control"] == "no-store"
    proxy = AIServiceClient()
    forward = AsyncMock(
        return_value=httpx.Response(
            200,
            json=value.model_dump(mode="json"),
        )
    )
    monkeypatch.setattr(proxy, "_make_request", forward)
    args = (actor.project_id, actor.id, agent)
    if operation == "save":
        args += (command,)
    elif operation == "original":
        args += (command.request_id,)
    assert await getattr(proxy, name)(*args) == value
    keys = (
        ("project_id", "agent_id", "actor_id")
        if operation == "read"
        else (
            "project_id",
            "agent_id",
            "configured_by",
            "request_id",
        )
    )
    for key in keys:
        forward.return_value = httpx.Response(
            200,
            json=value.model_copy(
                update={key: uuid4()},
            ).model_dump(mode="json"),
        )
        with pytest.raises(HTTPException) as error:
            await getattr(proxy, name)(*args)
        assert error.value.status_code == 502


@pytest.mark.asyncio
async def test_selection_validation_hides_private_extra_field_names(caplog):
    app, _ = application()
    app.include_router(module.router, prefix="/v1/ai/agents")
    marker = "PRIVATE-SELECTION-FIELD"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            f"/v1/ai/agents/{uuid4()}/device-script-selection",
            json={
                "request_id": str(uuid4()),
                "expected_revision": 0,
                "report_id": str(uuid4()),
                "review_id": str(uuid4()),
                marker: marker,
            },
        )
    assert response.status_code == 422 and marker not in response.text
    assert marker not in caplog.text
