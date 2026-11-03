"""Only scoped current review intentions can be displayed or continued."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4
import pytest
from fastapi import HTTPException
from app.schemas.agent_business_reviews import (
    BusinessReviewRequest,
    BusinessReviewChecks,
)
from app.schemas.agent_business_review_requests import BusinessReviewRequestState
from app.services import agent_business_trial_client as client
from tests.test_agent_business_review_client import Transport


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "valid",
        "project",
        "actor",
        "agent",
        "report",
        "request",
        "scope",
        "enable",
        "missing",
        "secret",
        "stale",
        "command_key",
        "checks_missing",
        "processing_missing",
        "processing_invalid",
    ],
)
async def test_saved_review_command_is_closed_and_belongs_to_original_scope(change):
    project, actor, agent, report, request = [uuid4() for _ in range(5)]
    command = BusinessReviewRequest(
        request_id=request,
        expected_latest_review_id=None,
        decision="passed",
        reason=None,
        checks=BusinessReviewChecks(
            ownership_checked=True, result_checked=True, readonly_scope_checked=True
        ),
    )
    state = BusinessReviewRequestState(
        project_id=project,
        actor_id=actor,
        agent_id=agent,
        report_id=report,
        request_id=request,
        status="pending",
        created_at=datetime.now(UTC),
        deadline_at=datetime.now(UTC) + timedelta(seconds=30),
        completed_at=None,
        checked_at=datetime.now(UTC),
        can_resume=True,
        processing=False,
        error_code=None,
        command=command,
        result=None,
    )
    body = state.model_dump(mode="json")
    if change in {"project", "actor", "agent", "report", "request"}:
        body[change + "_id"] = str(uuid4())
    if change == "scope":
        body["scope"] = "employee_business_trial"
    if change == "enable":
        body["can_enable"] = True
    if change == "missing":
        del body["can_resume"]
    if change == "secret":
        body["command"]["order_no"] = "PRIVATE-ORDER-123"
    if change == "stale":
        body["checked_at"] = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    if change == "command_key":
        body["command"]["request_id"] = str(uuid4())
    if change == "checks_missing":
        del body["command"]["checks"]["ownership_checked"]
    if change == "processing_missing":
        del body["processing"]
    if change == "processing_invalid":
        body["processing"] = True
    if change == "valid":
        assert (
            await client.review_request_state(
                Transport(body), project, actor, agent, report, request
            )
            == state
        )
    else:
        with pytest.raises(HTTPException):
            await client.review_request_state(
                Transport(body), project, actor, agent, report, request
            )


@pytest.mark.asyncio
async def test_cancel_response_cannot_leave_original_review_pending():
    project, actor, agent, report, request = [uuid4() for _ in range(5)]
    command = BusinessReviewRequest(
        request_id=request,
        expected_latest_review_id=None,
        decision="rejected",
        reason="wrong_result",
        checks=BusinessReviewChecks(),
    )
    state = BusinessReviewRequestState(
        project_id=project,
        actor_id=actor,
        agent_id=agent,
        report_id=report,
        request_id=request,
        status="pending",
        created_at=datetime.now(UTC),
        deadline_at=datetime.now(UTC) + timedelta(seconds=30),
        completed_at=None,
        checked_at=datetime.now(UTC),
        can_resume=True,
        processing=False,
        error_code=None,
        command=command,
        result=None,
    )
    with pytest.raises(HTTPException):
        await client.review_request_state(
            Transport(state.model_dump(mode="json")),
            project,
            actor,
            agent,
            report,
            request,
            cancel=True,
        )
