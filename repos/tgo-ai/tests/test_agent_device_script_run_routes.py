"""Private business routes inject trusted identities and redact all input."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1 import agent_device_script_runs as module
from app.config import settings
from app.schemas.agent_device_script_runs import ScriptRunView


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "identity", ["valid", "public", "missing", "wrong"]
)
@pytest.mark.parametrize("operation", ["start", "read", "cancel"])
async def test_private_business_run_uses_trusted_headers(
    monkeypatch,
    identity,
    operation,
):
    project, actor, agent, request, report, review = [
        uuid4() for _ in range(6)
    ]
    key, now = uuid4().hex + uuid4().hex, datetime.now(timezone.utc)
    monkeypatch.setattr(settings, "secret_key", key)
    value = ScriptRunView(
        id=uuid4(),
        project_id=project,
        actor_id=actor,
        agent_id=agent,
        request_id=request,
        report_id=report,
        review_id=review,
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
    action = AsyncMock(return_value=value)
    name = {"start": "start", "read": "get", "cancel": "cancel"}[
        operation
    ]
    constructor = Mock(return_value=Mock(**{name: action}))
    monkeypatch.setattr(module, "AgentDeviceScriptRuns", constructor)
    cases = object()
    app = FastAPI()
    app.include_router(module.router, prefix="/api/v1/agents")
    app.dependency_overrides[module.get_service] = lambda: cases
    headers = {
        "X-Internal-API-Key": key,
        "X-Project-Id": str(project),
        "X-Staff-Id": str(actor),
    }
    if identity == "public":
        headers = {"Authorization": "Bearer public-token"}
    elif identity == "missing":
        headers = {}
    elif identity == "wrong":
        headers["X-Internal-API-Key"] = "incorrect"
    path = f"/api/v1/agents/{agent}/device-script-runs"
    if operation != "start":
        path += f"/requests/{request}"
    if operation == "cancel":
        path += "/cancel"
    body = {
        "request_id": str(request),
        "report_id": str(report),
        "review_id": str(review),
    }
    if operation == "start":
        body["parameters"] = {"label": "private-fixture"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://test",
    ) as client:
        response = await client.request(
            "GET" if operation == "read" else "POST",
            path + f"?project_id={uuid4()}&actor_id={uuid4()}",
            headers=headers,
            json=None if operation == "read" else body,
        )
    assert response.status_code == (200 if identity == "valid" else 403)
    if identity == "valid":
        constructor.assert_called_once_with(cases)
        assert action.await_args.args[:3] == (project, actor, agent)
        assert response.headers["cache-control"] == "no-store"
    else:
        constructor.assert_not_called()
        action.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("place", ["value", "key", "extra", "source"])
async def test_private_validation_never_reflects_business_input(
    monkeypatch,
    place,
):
    key = uuid4().hex + uuid4().hex
    monkeypatch.setattr(settings, "secret_key", key)
    app = FastAPI()
    app.include_router(module.router, prefix="/api/v1/agents")
    app.dependency_overrides[module.get_service] = object
    marker = "PRIVATE-SCRIPT-RUN-INPUT"
    body = {
        "request_id": str(uuid4()),
        "report_id": str(uuid4()),
        "review_id": str(uuid4()),
        "parameters": {"label": "value"},
    }
    if place == "value":
        body["parameters"] = {"label": {"private": marker}}
    elif place == "key":
        body["parameters"] = {marker: "value"}
    elif place == "source":
        body["source"] = {"agent_execution_id": marker}
    else:
        body[marker] = "value"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            f"/api/v1/agents/{uuid4()}/device-script-runs",
            headers={
                "X-Internal-API-Key": key,
                "X-Project-Id": str(uuid4()),
                "X-Staff-Id": str(uuid4()),
            },
            json=body,
        )
    assert response.status_code == 422 and marker not in response.text
    assert response.headers["cache-control"] == "no-store"
