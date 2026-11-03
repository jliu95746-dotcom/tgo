"""The AI proxy carries trusted identity and validates each trial response."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.schemas.agent_trials import AgentTrialReport, AgentTrialRequest
from app.services.ai_client import AIServiceClient


def report_for(agent_id):
    return AgentTrialReport(
        id=uuid4(), agent_id=agent_id, request_key=uuid4(), status="completed",
        channel="web", question="材质？", answer="试聊结果",
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_trial_proxy_uses_trusted_identity_and_full_timeout(monkeypatch):
    client = AIServiceClient()
    project, staff, agent = uuid4(), uuid4(), uuid4()
    report = report_for(agent)
    request = AgentTrialRequest(request_key=report.request_key, message="材质？", timeout=60)
    transport = AsyncMock(return_value=httpx.Response(200, json=report.model_dump(mode="json")))
    monkeypatch.setattr(client, "_make_request", transport)
    assert await client.start_agent_trial(project, staff, agent, request) == report
    sent = transport.await_args.kwargs
    assert sent["extra_headers"]["X-Project-Id"] == str(project)
    assert sent["extra_headers"]["X-Staff-Id"] == str(staff)
    assert sent["extra_headers"]["X-Internal-API-Key"]
    assert sent["timeout"] >= 75
    assert not {"project_id", "staff_id", "provider_credentials"} & sent["json_data"].keys()


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["wrong_agent", "wrong_request", "malformed", "enable_override", "wrong_scope", "bad_revision"])
async def test_bad_trial_response_fails_closed_without_echo(monkeypatch, variant):
    client = AIServiceClient()
    project, staff, agent = uuid4(), uuid4(), uuid4()
    report = report_for(agent)
    body = report.model_dump(mode="json")
    if variant == "wrong_agent":
        body["agent_id"] = str(uuid4())
    elif variant == "wrong_request":
        body["request_key"] = str(uuid4())
    elif variant == "enable_override":
        body["can_enable"] = True
    elif variant == "wrong_scope":
        body["applicability"] = {"scope": "all_business_resources", "status": "current"}
    elif variant == "bad_revision":
        body["knowledge_snapshot"] = {"version": 1, "channel": "web", "binding_mode": "explicit",
            "captured_at": body["created_at"], "collections": [
                {"collection_id": str(uuid4()), "resource_revision": "secret-provider-value"},
            ]}
    response = httpx.Response(200, content=b"secret-upstream-value") if variant == "malformed" else httpx.Response(200, json=body)
    monkeypatch.setattr(client, "_make_request", AsyncMock(return_value=response))
    with pytest.raises(HTTPException) as error:
        await client.start_agent_trial(project, staff, agent, AgentTrialRequest(
            request_key=report.request_key, message="材质？",
        ))
    assert error.value.status_code == 502
    assert "secret-upstream-value" not in str(error.value.detail)


@pytest.mark.asyncio
async def test_report_reads_validate_identifiers_and_do_not_start_model(monkeypatch):
    client = AIServiceClient()
    project, staff, agent = uuid4(), uuid4(), uuid4()
    report = report_for(agent)
    transport = AsyncMock(return_value=httpx.Response(200, json=report.model_dump(mode="json")))
    monkeypatch.setattr(client, "_make_request", transport)
    assert await client.agent_trial_by_request(project, staff, agent, report.request_key) == report
    assert await client.get_agent_trial(project, staff, agent, report.id) == report
    assert await client.cancel_agent_trial(project, staff, agent, report.id) == report
    assert await client.match_agent_trial_request(project, staff, agent, AgentTrialRequest(
        request_key=report.request_key, message="材质？",
    )) == report
    assert all("/trials" in str(call.args[1]) for call in transport.await_args_list)


@pytest.mark.asyncio
async def test_upstream_errors_do_not_echo_provider_or_validation_input(monkeypatch):
    client = AIServiceClient()
    monkeypatch.setattr(client, "_make_request", AsyncMock(return_value=httpx.Response(
        409, json={"detail": "secret-upstream-value"},
    )))
    with pytest.raises(HTTPException) as error:
        await client.get_agent_trial(uuid4(), uuid4(), uuid4(), uuid4())
    assert error.value.status_code == 409
    assert "secret-upstream-value" not in str(error.value.detail)
