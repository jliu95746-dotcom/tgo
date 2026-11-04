"""Gateway rejects foreign, defaulted, stale and execution-granting evidence."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.schemas.agent_business_reviews import (
    BusinessReviewChecks,
    BusinessReviewRequest,
    BusinessReviewRecord,
    BusinessReviewState,
    AgentBusinessCapabilities,
)
from app.services import agent_business_trial_client as client


def fixture():
    project, actor, agent, report = [uuid4() for _ in range(4)]
    command = BusinessReviewRequest(
        request_id=uuid4(),
        expected_latest_review_id=None,
        decision="passed",
        reason=None,
        checks=BusinessReviewChecks(
            ownership_checked=True, result_checked=True, readonly_scope_checked=True
        ),
    )
    record = BusinessReviewRecord(
        id=uuid4(),
        project_id=project,
        agent_id=agent,
        report_id=report,
        connection_id=uuid4(),
        operation="order_query",
        reviewed_by=actor,
        request_id=command.request_id,
        revision=1,
        report_digest="c" * 64,
        configuration_fingerprint="b" * 64,
        decision="passed",
        checks=command.checks,
        reason=None,
        created_at=datetime.now(UTC),
        valid_until=datetime.now(UTC) + timedelta(hours=1),
    )
    return project, actor, agent, report, command, record


class Transport:
    def __init__(self, value):
        self.value = value

    def _trial_headers(self, p, a):
        return {"project": str(p), "actor": str(a)}

    async def _make_request(self, method, path, **kwargs):
        return httpx.Response(200, json=self.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "valid",
        "actor",
        "agent",
        "report",
        "project",
        "request",
        "enable",
        "scope",
        "missing",
        "criteria",
    ],
)
async def test_review_receipt_belongs_to_original_authenticated_request(change):
    project, actor, agent, report, command, record = fixture()
    body = record.model_dump(mode="json")
    if change in {"actor", "agent", "report", "project", "request"}:
        body["reviewed_by" if change == "actor" else change + "_id"] = str(uuid4())
    if change == "enable":
        body["can_enable"] = True
    if change == "scope":
        body["scope"] = "knowledge_case"
    if change == "missing":
        del body["can_enable"]
    if change == "criteria":
        del body["criteria_version"]
    if change == "valid":
        assert (
            await client.review(
                Transport(body), project, actor, agent, report, command=command
            )
        ).id == record.id
    else:
        with pytest.raises(HTTPException):
            await client.review(
                Transport(body), project, actor, agent, report, command=command
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["valid", "actor", "stale", "missing", "contradiction"]
)
async def test_current_review_state_has_fresh_owner_and_full_contract(change):
    project, actor, agent, report, command, record = fixture()
    body = BusinessReviewState(
        project_id=project,
        agent_id=agent,
        report_id=report,
        checked_by=actor,
        checked_at=datetime.now(UTC),
        status="current",
        review_status="passed",
        issues=[],
        can_review=True,
        valid_until=record.valid_until,
        latest_review=record,
        reviews=[record],
    ).model_dump(mode="json")
    if change == "actor":
        body["checked_by"] = str(uuid4())
    if change == "stale":
        body["checked_at"] = (datetime.now(UTC) - timedelta(minutes=2)).isoformat()
    if change == "missing":
        del body["scope"]
    if change == "contradiction":
        body["latest_review"]["decision"] = "rejected"
    if change == "valid":
        assert (
            await client.review_state(Transport(body), project, actor, agent, report)
        ).review_status == "passed"
    else:
        with pytest.raises(HTTPException):
            await client.review_state(Transport(body), project, actor, agent, report)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["valid", "execute", "enable", "actor", "expired"])
async def test_business_review_summary_is_not_a_formal_execution_grant(change):
    project, actor, agent, report, command, record = fixture()
    body = AgentBusinessCapabilities(
        project_id=project,
        agent_id=agent,
        checked_by=actor,
        checked_at=datetime.now(UTC),
        capabilities=[
            {
                "binding_id": uuid4(),
                "binding_revision": 1,
                "connection_id": record.connection_id,
                "operation": "order_query",
                "status": "passed",
                "report_id": report,
                "review_id": record.id,
                "valid_until": record.valid_until,
                "issues": [],
            }
        ],
    ).model_dump(mode="json")
    if change in {"execute", "enable"}:
        body["can_" + change] = True
    if change == "actor":
        body["checked_by"] = str(uuid4())
    if change == "expired":
        body["capabilities"][0]["valid_until"] = (
            datetime.now(UTC) - timedelta(seconds=1)
        ).isoformat()
    if change == "valid":
        assert (
            await client.capabilities(Transport(body), project, actor, agent)
        ).can_execute is False
    else:
        with pytest.raises(HTTPException):
            await client.capabilities(Transport(body), project, actor, agent)
