"""The gateway owns cases and rejects forged or incomplete coverage."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.schemas.agent_knowledge_capabilities import KnowledgeCapabilityQuery
from app.services.agent_trial_review_client import capabilities
from app.services.ai_client import AIServiceClient
from app.services.knowledge_review_cases import list_cases
from app.schemas.agent_knowledge_capabilities import AgentKnowledgeCapabilities
from app.services import agent_trial_reviews
from tests.test_agent_trial_routes import application


def result(agent, actor):
    groups = []
    for template in ("knowledge", "aftersales"):
        rows = [
            {"case": case.model_dump(mode="json"), "status": "not_tested"}
            for case in list_cases()
            if case.template_id == template
        ]
        groups.append(
            {
                "template_id": template,
                "template_version": 1,
                "status": "not_tested",
                "answer_cases_passed": 0,
                "answer_cases_required": 2,
                "safety_cases_passed": 0,
                "safety_cases_required": 1,
                "cases": rows,
            }
        )
    return {
        "agent_id": str(agent),
        "reviewed_by": str(actor),
        "channel": "web",
        "scope": "knowledge_templates",
        "can_enable": False,
        "enabled": False,
        "knowledge_status": "available",
        "checked_at": datetime.now(UTC).isoformat(),
        "capabilities": groups,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "user"])
async def test_public_read_uses_authenticated_identity(monkeypatch, role):
    app, actor = application(role)
    agent = uuid4()
    reader = AsyncMock(
        return_value=AgentKnowledgeCapabilities.model_validate(result(agent, actor.id))
    )
    monkeypatch.setattr(agent_trial_reviews, "capabilities", reader)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/v1/ai/agents/{agent}/knowledge-capabilities")
    assert response.status_code == (200 if role == "admin" else 403)
    if role == "admin":
        assert reader.call_args.args[:3] == (actor.project_id, actor.id, agent)
    else:
        reader.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "valid",
        "actor",
        "agent",
        "channel",
        "enable",
        "missing",
        "duplicate",
        "criterion",
        "counts",
        "passed",
        "upstream",
    ],
)
async def test_transport_checks_identity_complete_cases_and_counts(change):
    project, actor, agent = uuid4(), uuid4(), uuid4()
    body = result(agent, actor)
    if change in {"actor", "agent"}:
        body["reviewed_by" if change == "actor" else "agent_id"] = str(uuid4())
    elif change == "channel":
        body["channel"] = "wecom_kf"
    elif change == "enable":
        body["can_enable"] = True
    elif change == "missing":
        body["capabilities"].pop()
    elif change == "duplicate":
        body["capabilities"].append(body["capabilities"][0])
    elif change == "criterion":
        body["capabilities"][0]["cases"][0]["case"]["question"] = "伪造判据"
    elif change == "counts":
        body["capabilities"][0]["answer_cases_passed"] = 2
    elif change == "passed":
        body["capabilities"][0]["status"] = "passed"
    client = AIServiceClient()
    client._make_request = AsyncMock(
        return_value=httpx.Response(503 if change == "upstream" else 200, json=body)
    )
    query = KnowledgeCapabilityQuery(cases=list_cases())
    if change == "valid":
        value = await capabilities(client, project, actor, agent, query)
        assert not value.can_enable and len(value.capabilities) == 2
        kwargs = client._make_request.call_args.kwargs
        assert kwargs["json_data"]["cases"] == query.model_dump(mode="json")["cases"]
        assert kwargs["extra_headers"]["X-Project-Id"] == str(project) and kwargs[
            "extra_headers"
        ]["X-Staff-Id"] == str(actor)
    else:
        with pytest.raises(HTTPException) as failure:
            await capabilities(client, project, actor, agent, query)
        assert failure.value.status_code == (503 if change == "upstream" else 502)
        assert "伪造判据" not in failure.value.detail
