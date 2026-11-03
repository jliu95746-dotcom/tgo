"""The snapshot is fresh metadata, not an activation or execution grant."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4
import pytest
from fastapi import HTTPException
from app.schemas.agent_business_requests import BusinessEmployeeCurrentState
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
        "scope",
        "enable",
        "missing",
        "stale",
        "secret",
        "active",
    ],
)
async def test_current_employee_snapshot_is_exact_and_fresh(change):
    project, actor, agent = [uuid4() for _ in range(3)]
    state = BusinessEmployeeCurrentState(
        project_id=project,
        actor_id=actor,
        agent_id=agent,
        active=False,
        checked_at=datetime.now(UTC),
    )
    body = state.model_dump(mode="json")
    if change in {"project", "actor", "agent"}:
        body[change + "_id"] = str(uuid4())
    if change == "scope":
        body["scope"] = "employee_activation"
    if change == "enable":
        body["can_enable"] = True
    if change == "missing":
        del body["can_enable"]
    if change == "stale":
        body["checked_at"] = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    if change == "secret":
        body["order_no"] = "PRIVATE"
    if change == "active":
        body["active"] = "false"
    if change == "valid":
        assert (
            await client.current_employee(Transport(body), project, actor, agent)
            == state
        )
    else:
        with pytest.raises(HTTPException):
            await client.current_employee(Transport(body), project, actor, agent)
