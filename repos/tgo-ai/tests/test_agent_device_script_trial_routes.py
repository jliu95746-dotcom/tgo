"""Private trials bind trusted identities and redact invalid private inputs."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1 import agent_device_script_trials as module
from app.config import settings
from app.schemas.agent_device_script_trials import ScriptTrialReport
from app.schemas.agent_device_script_requests import ScriptTrialRequestState


@pytest.mark.parametrize(
    "identity", ["valid", "missing", "public", "wrong_key", "wrong_id"]
)
async def test_trial_identity_cannot_be_supplied_by_a_public_client(
    monkeypatch, identity
):
    project, actor, employee, request, report = (uuid4() for _ in range(5))
    key = uuid4().hex + uuid4().hex
    monkeypatch.setattr(settings, "secret_key", key)
    now = datetime.now(timezone.utc)
    result = ScriptTrialReport(
        id=report,
        project_id=project,
        actor_id=actor,
        agent_id=employee,
        request_id=request,
        device_id=uuid4(),
        execution_id=uuid4(),
        binding_id=uuid4(),
        binding_revision=1,
        policy_digest="a" * 64,
        grant_digest="b" * 64,
        expected_digest="c" * 64,
        result_digest=None,
        status="unknown",
        error_code="result_unknown",
        remaining_runs=1,
        applicability="current",
        created_at=now,
        completed_at=None,
        expires_at=now + timedelta(hours=1),
        dispatch_claimed_at=now,
    )
    service = Mock(
        start=AsyncMock(return_value=result),
        get=AsyncMock(return_value=result),
        by_request=AsyncMock(return_value=result),
    )
    request_result = ScriptTrialRequestState(
        project_id=project, actor_id=actor, agent_id=employee,
        request_id=request, binding_id=result.binding_id, binding_revision=1,
        status="ready", stop_status="none", created_at=now,
        deadline_at=now + timedelta(seconds=30), completed_at=now,
        error_code=None, report=result,
    )
    requests = Mock(
        state=AsyncMock(return_value=request_result),
        cancel=AsyncMock(return_value=request_result),
    )
    monkeypatch.setattr(module, "ScriptTrialRequests", lambda _: requests)
    app = FastAPI()
    app.include_router(module.router, prefix="/api/v1/agents")
    app.dependency_overrides[module.get_service] = lambda: service
    headers = {
        "X-Internal-API-Key": key,
        "X-Project-Id": str(project),
        "X-Staff-Id": str(actor),
    }
    if identity == "missing":
        headers = {}
    elif identity == "public":
        headers = {"Authorization": "Bearer public-token"}
    elif identity == "wrong_key":
        headers["X-Internal-API-Key"] = "wrong"
    elif identity == "wrong_id":
        headers["X-Staff-Id"] = "not-an-id"
    body = dict(
        request_id=str(request),
        binding_id=str(result.binding_id),
        binding_revision=1,
        parameters={"label": "private-marker"},
        expected_fields={"ready": True},
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        path = f"/api/v1/agents/{employee}/device-script-trials"
        responses = [
            await client.post(
                path + f"?actor_id={uuid4()}", headers=headers, json=body
            ),
            await client.get(path + f"/{report}", headers=headers),
            await client.get(path + f"/requests/{request}", headers=headers),
            await client.get(
                path + f"/requests/{request}/state", headers=headers
            ),
            await client.post(
                path + f"/requests/{request}/cancel", headers=headers,
                json={key: body[key] for key in (
                    "request_id", "binding_id", "binding_revision"
                )},
            ),
        ]
        if identity == "valid":
            for response in responses:
                assert (
                    response.status_code == 200
                    and response.json()["can_enable"] is False
                )
                assert "private-marker" not in response.text
            assert service.start.await_args.args[:3] == (
                project,
                actor,
                employee,
            )
            service.get.assert_awaited_once_with(
                project, actor, employee, report
            )
            service.by_request.assert_awaited_once_with(
                project, actor, employee, request
            )
            requests.state.assert_awaited_once_with(
                project, actor, employee, request
            )
            assert requests.cancel.await_args.args[:3] == (
                project, actor, employee
            )
            bad = {
                **body,
                "parameters": {"label": {"secret": "private-marker"}},
            }
            response = await client.post(path, headers=headers, json=bad)
            assert (
                response.status_code == 422
                and "private-marker" not in response.text
            )
            assert service.start.await_count == 1
        else:
            assert all(response.status_code == 403 for response in responses)
            service.start.assert_not_awaited()
            service.get.assert_not_awaited()
            service.by_request.assert_not_awaited()
            requests.state.assert_not_awaited()
            requests.cancel.assert_not_awaited()
