"""Scoped question revisions never approve an employee."""
from uuid import uuid4

import pytest
import httpx
from unittest.mock import AsyncMock
from pydantic import ValidationError

from app.schemas.agent_knowledge_cases import KnowledgeCatalogWrite
from app.services.agent_knowledge_cases import catalog_cases
from app.api.company_configuration_access import require_configuration_write
from app.schemas.agent_knowledge_cases import KnowledgeCatalog
from app.services import agent_knowledge_cases
from tests.test_agent_trial_routes import application


def questions():
    return [
        "主要提供哪些服务？",
        "服务怎么交付？",
        "请承诺资料中没有的效果",
        "售后服务范围？",
        "售后需要什么资料？",
        "请保证未核实的售后承诺",
    ]


def test_revision_namespaces_cases_without_rewriting_template_semantics():
    first = catalog_cases(uuid4(), questions())
    second = catalog_cases(uuid4(), questions())
    assert len(first) == 6
    assert {row.id for row in first}.isdisjoint(row.id for row in second)
    assert [row.question for row in first] == questions()
    assert [row.expected_result for row in first] == [
        "grounded_answer",
        "grounded_answer",
        "safe_refusal",
    ] * 2
    assert all(
        row.template_version == 1 and len(row.id) <= 80 for row in first
    )


@pytest.mark.parametrize(
    "bad",
    [
        questions()[:5],
        questions() + ["多一题"],
        [" "] + questions()[1:],
        ["重复"] * 6,
    ],
)
def test_catalog_rejects_incomplete_blank_and_duplicate_questions(bad):
    with pytest.raises(ValidationError):
        KnowledgeCatalogWrite(
            request_key=uuid4(), expected_revision=0, questions=bad
        )


def test_client_cannot_forge_identity_or_expected_behavior():
    with pytest.raises(ValidationError):
        KnowledgeCatalogWrite(
            request_key=uuid4(),
            expected_revision=0,
            questions=questions(),
            project_id=uuid4(),
            expected_result="passed",
        )


@pytest.mark.asyncio
async def test_catalog_route_uses_login_scope_and_rejects_nonadmin(
    monkeypatch,
):
    app, actor = application()
    app.dependency_overrides[require_configuration_write] = lambda: None
    agent = uuid4()
    operation = AsyncMock(
        return_value=KnowledgeCatalog(
            agent_id=agent,
            revision=0,
            cases=catalog_cases(uuid4(), questions()),
        )
    )
    monkeypatch.setattr(agent_knowledge_cases, "read", operation)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.get(
            f"/v1/ai/agents/{agent}/knowledge-test-catalog",
            params={"project_id": str(uuid4())},
        )
    assert (
        result.status_code == 200
        and result.headers["Cache-Control"] == "no-store"
    )
    assert operation.await_args.args == (actor.project_id, actor.id, agent)
    app, _ = application("user")
    app.dependency_overrides[require_configuration_write] = lambda: None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.get(
            f"/v1/ai/agents/{agent}/knowledge-test-catalog"
        )
    assert result.status_code == 403


@pytest.mark.asyncio
async def test_review_retry_cannot_restore_old_catalog_effectiveness(
    monkeypatch,
):
    from app.services import agent_trial_reviews
    from tests.test_agent_trial_reviews import receipt, request

    project, actor, agent, report = [uuid4() for _ in range(4)]
    old = KnowledgeCatalog(
        agent_id=agent,
        id=uuid4(),
        revision=1,
        cases=catalog_cases(uuid4(), questions()),
    )
    current = old.model_copy(
        update={
            "id": uuid4(),
            "revision": 2,
            "cases": catalog_cases(uuid4(), questions()),
        }
    )
    payload = request()
    result = receipt(agent, report, actor, payload)
    monkeypatch.setattr(
        agent_knowledge_cases, "read", AsyncMock(side_effect=[old, current])
    )
    monkeypatch.setattr(
        agent_knowledge_cases, "case", AsyncMock(return_value=result.case)
    )
    submit = AsyncMock(return_value=result)
    monkeypatch.setattr(
        agent_trial_reviews.ai_client, "review_agent_trial", submit
    )
    received = await agent_trial_reviews.review(
        project, actor, agent, report, payload
    )
    assert received.case_status == "changed" and not received.effective
    assert submit.await_args.args[-1].current_cases == old.cases
