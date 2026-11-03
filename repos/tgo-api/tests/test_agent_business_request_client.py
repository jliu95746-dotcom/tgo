"""Request recovery/control preserves original scope and metadata-only receipts."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from app.schemas.agent_business_requests import (
    BusinessRequestControl,
    BusinessRequestState,
)
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
        "request",
        "scope",
        "enable",
        "secret",
        "missing",
        "operation",
        "preparing",
    ],
)
async def test_original_request_cancel_reply_cannot_change_scope(change):
    project, actor, agent, request, connection = [uuid4() for _ in range(5)]
    command = BusinessRequestControl(
        request_id=request, connection_id=connection, operation="order_query"
    )
    state = BusinessRequestState(
        **command.model_dump(),
        project_id=project,
        actor_id=actor,
        agent_id=agent,
        status="cancelled",
        created_at=datetime.now(UTC),
        deadline_at=datetime.now(UTC) + timedelta(seconds=15),
        completed_at=datetime.now(UTC),
        error_code="cancelled",
        report=None,
    )
    body = state.model_dump(mode="json")
    if change in {"project", "actor", "agent", "request"}:
        body[change + "_id"] = str(uuid4())
    if change == "scope":
        body["scope"] = "employee_business_trial"
    if change == "enable":
        body["can_enable"] = True
    if change == "secret":
        body["order_no"] = "PRIVATE-ORDER-123"
    if change == "missing":
        del body["report"]
    if change == "operation":
        body["operation"] = "logistics_query"
    if change == "preparing":
        body.update(status="preparing", completed_at=None, error_code=None)
    if change == "valid":
        result = await client.request_state(
            Transport(body), project, actor, agent, request, cancel=command
        )
        assert result == state and result.can_enable is False
    else:
        with pytest.raises(HTTPException):
            await client.request_state(
                Transport(body), project, actor, agent, request, cancel=command
            )


@pytest.mark.asyncio
async def test_cancel_command_id_must_match_route_before_transport():
    project, actor, agent, request, connection = [uuid4() for _ in range(5)]
    command = BusinessRequestControl(
        request_id=uuid4(), connection_id=connection, operation="order_query"
    )
    with pytest.raises(HTTPException) as error:
        await client.request_state(
            Transport({}), project, actor, agent, request, cancel=command
        )
    assert error.value.status_code == 409
