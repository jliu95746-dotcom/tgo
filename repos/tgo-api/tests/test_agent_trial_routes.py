"""Public trials require the logged-in administrator and never accept identity overrides."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError

from app.api.v1.endpoints.agent_trials import router
from app.core.exceptions import validation_exception_handler
from app.core.security import get_current_active_user
from app.schemas.agent_trials import AgentTrialReport
from app.services import agent_trials


def application(role="admin"):
    actor = SimpleNamespace(id=uuid4(), project_id=uuid4(), role=role, username="synthetic")
    app = FastAPI()
    app.include_router(router, prefix="/v1/ai/agents")
    app.dependency_overrides[get_current_active_user] = lambda: actor
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    return app, actor


def report_for(agent):
    return AgentTrialReport(id=uuid4(), agent_id=agent, request_key=uuid4(), status="completed",
                            channel="web", question="材质？", answer="无法确认", created_at=datetime.now(timezone.utc))


@pytest.mark.asyncio
async def test_start_uses_only_logged_in_identity(monkeypatch):
    app, actor = application()
    report = report_for(uuid4())
    operation = AsyncMock(return_value=report)
    monkeypatch.setattr(agent_trials, "start", operation)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/v1/ai/agents/{report.agent_id}/trials", params={"project_id": str(uuid4())},
                                     headers={"X-Project-Id": str(uuid4()), "X-Staff-Id": str(uuid4())},
                                     json={"request_key": str(report.request_key), "message": "材质？"})
    assert response.status_code == 200 and response.json()["can_enable"] is False
    assert operation.await_args.args[:3] == (actor.project_id, actor.id, report.agent_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("method,suffix,body", [
    ("POST", "", {"request_key": str(uuid4()), "message": "材质？"}),
    ("GET", "", None),
    ("POST", "/lookup", {"request_key": str(uuid4()), "message": "材质？"}),
    ("GET", "/requests/" + str(uuid4()), None),
    ("GET", "/" + str(uuid4()), None),
    ("POST", "/" + str(uuid4()) + "/cancel", None),
])
async def test_all_endpoints_require_admin(method, suffix, body):
    app, _ = application("user")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(method, f"/v1/ai/agents/{uuid4()}/trials{suffix}", json=body)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_forbidden_fields_and_bad_values_never_echo_input(monkeypatch):
    app, _ = application()
    operation = AsyncMock()
    monkeypatch.setattr(agent_trials, "start", operation)
    marker = "secret-value-must-not-echo"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for body in [{"request_key": str(uuid4()), "message": "材质？", "provider_credentials": marker},
                     {"request_key": marker, "message": "材质？"}]:
            response = await client.post(f"/v1/ai/agents/{uuid4()}/trials", json=body)
            assert response.status_code == 422 and marker not in response.text
    operation.assert_not_awaited()


@pytest.mark.asyncio
async def test_reads_and_cancel_keep_creator_and_agent_scope(monkeypatch):
    app, actor = application()
    report = report_for(uuid4())
    read, cancel = AsyncMock(return_value=report), AsyncMock(return_value=report)
    monkeypatch.setattr(agent_trials, "get", read)
    monkeypatch.setattr(agent_trials, "cancel", cancel)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        path = f"/v1/ai/agents/{report.agent_id}/trials/{report.id}"
        assert (await client.get(path)).status_code == 200
        assert (await client.post(path + "/cancel")).status_code == 200
        read.side_effect = HTTPException(404, "未找到试聊报告")
        assert (await client.get(path)).status_code == 404
    read.assert_awaited_with(actor.project_id, actor.id, report.agent_id, report.id)
    cancel.assert_awaited_once_with(actor.project_id, actor.id, report.agent_id, report.id)
