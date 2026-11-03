"""The public reviewer cannot forge identity, definitions or activation."""
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.schemas.agent_trial_reviews import KnowledgeReviewCommand, KnowledgeReviewRequest, KnowledgeReviewResponse, KnowledgeReviewChecks
from app.schemas.agent_trials import TrialApplicability
from app.services.ai_client import AIServiceClient
from app.services import agent_trial_reviews
from app.services.knowledge_review_cases import get_case, list_cases
from tests.test_agent_trial_routes import application


def request(case_id="knowledge.v1.1"):
    return KnowledgeReviewRequest(request_key=uuid4(), case_id=case_id, decision="passed",
        checks=KnowledgeReviewChecks(answer_correct=True, evidence_checked=True, no_unverified_claims=True, expected_behavior=True))


def receipt(agent, report, actor, payload):
    return KnowledgeReviewResponse(id=uuid4(), revision=1, agent_id=agent, report_id=report, request_key=payload.request_key,
        reviewed_by=actor, decision="passed", checks=payload.checks, note="", case=get_case(payload.case_id),
        case_status="current", applicability=TrialApplicability(status="current"), effective=True,
        created_at=datetime.now(UTC), valid_until=datetime.now(UTC)+timedelta(days=7))


def test_cases_reuse_owner_template_versions_and_include_safe_boundaries():
    cases=list_cases()
    assert len(cases)==6 and len({case.id for case in cases})==6
    assert sum(case.expected_result=="safe_refusal" for case in cases)==2
    assert get_case("knowledge.v1.1").question=="这款包是什么材质？"
    with pytest.raises(HTTPException): get_case("client-created-case")


@pytest.mark.asyncio
async def test_public_review_uses_login_identity_and_existing_trial_route(monkeypatch):
    app, actor=application()
    agent, report=uuid4(),uuid4()
    payload=request()
    submit=AsyncMock(return_value=receipt(agent,report,actor.id,payload))
    monkeypatch.setattr(agent_trial_reviews,"review",submit)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
        response=await client.post(f"/v1/ai/agents/{agent}/trials/{report}/reviews",json=payload.model_dump(mode="json"))
    assert response.status_code==200 and response.json()["can_enable"] is False
    assert submit.await_args.args[:4]==(actor.project_id,actor.id,agent,report)


@pytest.mark.asyncio
@pytest.mark.parametrize("field",["project_id","reviewed_by","case","effective","can_enable","created_at"])
async def test_public_review_cannot_supply_trusted_evidence_or_identity(monkeypatch,field):
    app,_=application()
    submit=AsyncMock()
    monkeypatch.setattr(agent_trial_reviews,"review",submit)
    body=request().model_dump(mode="json");body[field]="secret-forged-value"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
        response=await client.post(f"/v1/ai/agents/{uuid4()}/trials/{uuid4()}/reviews",json=body)
    assert response.status_code==422 and "secret-forged-value" not in response.text
    submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_nonadministrator_cannot_review(monkeypatch):
    app,_=application("user")
    submit=AsyncMock();monkeypatch.setattr(agent_trial_reviews,"review",submit)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
        response=await client.post(f"/v1/ai/agents/{uuid4()}/trials/{uuid4()}/reviews",json=request().model_dump(mode="json"))
    assert response.status_code==403
    submit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("variant",["valid","wrong_actor","wrong_case","wrong_report","enable","upstream_error"])
async def test_review_transport_delegates_identity_and_validates_receipt(monkeypatch,variant):
    client=AIServiceClient();project,actor,agent,report=[uuid4() for _ in range(4)]
    payload=request();command=KnowledgeReviewCommand(**payload.model_dump(),case=get_case(payload.case_id))
    body=receipt(agent,report,actor,payload).model_dump(mode="json")
    if variant=="wrong_actor":body["reviewed_by"]=str(uuid4())
    elif variant=="wrong_report":body["report_id"]=str(uuid4())
    elif variant=="wrong_case":body["case"]["question"]="另一份定义"
    elif variant=="enable":body["can_enable"]=True
    response=httpx.Response(500,json={"detail":"secret-upstream"}) if variant=="upstream_error" else httpx.Response(200,json=body)
    transport=AsyncMock(return_value=response);monkeypatch.setattr(client,"_make_request",transport)
    if variant=="valid":
        result=await client.review_agent_trial(project,actor,agent,report,command)
        assert result.effective and result.can_enable is False
        assert transport.await_args.kwargs["extra_headers"]["X-Staff-Id"]==str(actor)
        assert transport.await_args.kwargs["json_data"]["case"]==command.case.model_dump(mode="json")
    else:
        with pytest.raises(HTTPException) as failure:
            await client.review_agent_trial(project,actor,agent,report,command)
        assert "secret-upstream" not in str(failure.value.detail)
