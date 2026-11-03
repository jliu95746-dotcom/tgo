"""Only the trusted API may access inactive-employee trials and reports."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1 import agent_trials
from app.schemas.agent_trials import AgentTrialReport


def application(monkeypatch):
    project, staff, employee = uuid4(), uuid4(), uuid4()
    monkeypatch.setattr(agent_trials.settings, "secret_key", "isolated-test-service-key")
    report = AgentTrialReport(
        id=uuid4(), agent_id=employee, request_key=uuid4(), status="completed",
        channel="web", question="材质？", answer="测试答复", created_at=datetime.now(UTC),
    )
    service = Mock(
        start=AsyncMock(return_value=report), get=AsyncMock(return_value=report),
        list=AsyncMock(return_value=[report]), cancel=AsyncMock(return_value=report),
        by_request=AsyncMock(return_value=report),
        match_request=AsyncMock(return_value=report),
    )
    authorize = AsyncMock()

    async def start(*args, **kwargs):
        await kwargs["authorize"]()
        return report

    service.start.side_effect = start
    monkeypatch.setattr(agent_trials, "authorize", authorize)
    app = FastAPI()
    app.include_router(agent_trials.router, prefix="/api/v1/agents")
    app.dependency_overrides[agent_trials.get_trial_service] = lambda: service
    headers = {
        "X-Internal-API-Key": "isolated-test-service-key",
        "X-Project-Id": str(project), "X-Staff-Id": str(staff),
    }
    return app, service, authorize, report, project, staff, headers


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [{}, {"Authorization": "Bearer public-test-token"},
    {"X-API-Key": "ak_public-test"}, {"X-Internal-API-Key": "wrong"},
    {"X-Project-Id": "not-a-uuid"}, {"X-Staff-Id": "not-a-uuid"},
])
async def test_internal_trial_identity_has_no_public_auth_fallback(monkeypatch, invalid):
    app, service, _, report, _, _, headers = application(monkeypatch)
    if any(name in invalid for name in ("X-Project-Id", "X-Staff-Id")):
        headers.update(invalid)
    elif invalid.get("X-Internal-API-Key"):
        headers.update(invalid)
    else:
        headers = invalid
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/agents/{report.agent_id}/trials", headers=headers)
    assert response.status_code == 403
    service.list.assert_not_awaited()


@pytest.mark.asyncio
async def test_routes_derive_owner_identity_and_keep_quota_before_execution(monkeypatch):
    app, service, quota, report, project, staff, headers = application(monkeypatch)
    base = f"/api/v1/agents/{report.agent_id}/trials"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        response = await client.post(base, headers=headers, json={
            "request_key": str(report.request_key), "message": "材质？",
        })
        assert response.status_code == 200
        assert response.json()["assessment"] == "not_reviewed"
        assert (await client.post(f"{base}/lookup", headers=headers, json={
            "request_key": str(report.request_key), "message": "材质？",
        })).status_code == 200
        for path in (base, f"{base}/{report.id}", f"{base}/requests/{report.request_key}"):
            assert (await client.get(path, headers=headers)).status_code == 200
        assert (await client.post(f"{base}/{report.id}/cancel", headers=headers)).status_code == 200
    assert service.start.await_args.args[:3] == (project, staff, report.agent_id)
    quota.assert_awaited_once()
    assert quota.await_args.args[1] == project
    assert service.cancel.await_args.args == (project, staff, report.agent_id, report.id)


@pytest.mark.asyncio
async def test_validation_response_does_not_echo_credentials_or_question(monkeypatch):
    app, service, _, report, _, _, headers = application(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        response = await client.post(f"/api/v1/agents/{report.agent_id}/trials", headers=headers, json={
            "request_key": str(report.request_key), "message": "private-customer-question",
            "provider_credentials": {"api_key": "secret-test-value"},
        })
    assert response.status_code == 422
    assert "secret-test-value" not in response.text
    assert "private-customer-question" not in response.text
    service.start.assert_not_awaited()
