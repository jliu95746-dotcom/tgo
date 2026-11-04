"""Preserve staff ownership and exact review receipts through the gateway."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import agent_device_script_cases
from app.schemas.agent_device_script_reviews import (
    ScriptReviewRecord,
    ScriptReviewState,
    ScriptReviewWrite,
    ScriptRunCheck,
)
from app.services.ai_client import AIServiceClient, ai_client
from tests.test_agent_device_script_trials import application


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "user"])
async def test_review_write_uses_staff_identity_and_no_store(
    monkeypatch, role
):
    app, staff = application(role)
    app.include_router(
        agent_device_script_cases.router, prefix="/v1/ai/agents"
    )
    project, actor, agent, report = (
        staff.project_id,
        staff.id,
        uuid4(),
        uuid4(),
    )
    command = ScriptReviewWrite(
        case_version_id=uuid4(),
        case_digest="a" * 64,
        result_digest="b" * 64,
        decision="approved",
    )
    now = datetime.now(timezone.utc)
    record = ScriptReviewRecord(
        **command.model_dump(),
        id=uuid4(),
        reviewed_at=now,
        expires_at=now + timedelta(hours=1),
        revoked_at=None,
    )
    state = ScriptReviewState(
        project_id=project,
        actor_id=actor,
        agent_id=agent,
        report_id=report,
        status="approved",
        review=record,
    )
    forward = AsyncMock(return_value=state)
    monkeypatch.setattr(
        ai_client, "agent_device_script_review", forward
    )
    path = f"/v1/ai/agents/{agent}/device-script-trials/{report}/review"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            path, json=command.model_dump(mode="json")
        )
    assert response.status_code == (200 if role == "admin" else 403)
    if role == "admin":
        forward.assert_awaited_once_with(
            project, actor, agent, report, command=command
        )
        assert response.headers["cache-control"] == "no-store"
    else:
        forward.assert_not_awaited()


@pytest.mark.asyncio
async def test_proxy_requires_exact_review_and_revocation(monkeypatch):
    client = AIServiceClient()
    project, actor, agent, report = [uuid4() for _ in range(4)]
    command = ScriptReviewWrite(
        case_version_id=uuid4(),
        case_digest="a" * 64,
        result_digest="b" * 64,
        decision="approved",
    )
    now = datetime.now(timezone.utc)
    record = ScriptReviewRecord(
        **command.model_dump(),
        id=uuid4(),
        reviewed_at=now,
        expires_at=now + timedelta(hours=1),
        revoked_at=None,
    )
    state = ScriptReviewState(
        project_id=project,
        actor_id=actor,
        agent_id=agent,
        report_id=report,
        status="approved",
        review=record,
    )
    request = AsyncMock(
        return_value=httpx.Response(
            200, json=state.model_dump(mode="json")
        )
    )
    monkeypatch.setattr(client, "_make_request", request)
    scope = project, actor, agent, report
    assert (
        await client.agent_device_script_review(*scope, command=command)
        == state
    )
    with pytest.raises(HTTPException) as unconfirmed:
        await client.agent_device_script_review(
            *scope, revoke=record.id
        )
    assert unconfirmed.value.status_code == 502
    state = state.model_copy(
        update={
            "status": "revoked",
            "review": record.model_copy(update={"revoked_at": now}),
        }
    )
    request.return_value = httpx.Response(
        200, json=state.model_dump(mode="json")
    )
    assert (
        await client.agent_device_script_review(
            *scope, revoke=record.id
        )
        == state
    )
    with pytest.raises(HTTPException) as foreign:
        await client.agent_device_script_review(
            project, uuid4(), agent, report
        )
    assert foreign.value.status_code == 502


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "user"])
async def test_run_check_uses_current_admin_and_validates_scope(
    monkeypatch, role
):
    app, staff = application(role)
    app.include_router(
        agent_device_script_cases.router, prefix="/v1/ai/agents"
    )
    project, actor, agent, report = (
        staff.project_id,
        staff.id,
        uuid4(),
        uuid4(),
    )
    value = ScriptRunCheck(
        review=ScriptReviewState(
            project_id=project,
            actor_id=actor,
            agent_id=agent,
            report_id=report,
            status="unreviewed",
            review=None,
        ),
        status="review_required",
        remaining_runs=None,
        checked_at=datetime.now(timezone.utc),
        valid_until=None,
    )
    forward = AsyncMock(return_value=value)
    monkeypatch.setattr(
        ai_client, "agent_device_script_run_check", forward
    )
    path = (
        f"/v1/ai/agents/{agent}/device-script-trials/{report}"
        "/review/run-check"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://test",
    ) as client:
        response = await client.get(path)
    assert response.status_code == (200 if role == "admin" else 403)
    if role == "user":
        forward.assert_not_awaited()
        return
    assert response.headers["cache-control"] == "no-store"
    forward.assert_awaited_once_with(project, actor, agent, report)
    proxy = AIServiceClient()
    request = AsyncMock(
        return_value=httpx.Response(
            200,
            json=value.model_dump(mode="json"),
        )
    )
    monkeypatch.setattr(proxy, "_make_request", request)
    assert (
        await proxy.agent_device_script_run_check(
            project,
            actor,
            agent,
            report,
        )
        == value
    )
    for foreign_scope in (
        (uuid4(), actor, agent, report),
        (project, uuid4(), agent, report),
        (project, actor, uuid4(), report),
        (project, actor, agent, uuid4()),
    ):
        with pytest.raises(HTTPException) as error:
            await proxy.agent_device_script_run_check(*foreign_scope)
        assert error.value.status_code == 502
