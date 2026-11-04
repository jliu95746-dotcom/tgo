"""The internal assistant cannot substitute stale or foreign approval data."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.exceptions import ConflictError
from app.models.agent_activation_policy import AgentActivationPolicy
from app.services.agent_device_script_activation import (
    script_activation_evidence,
)
from tests.test_employee_device_script_tools import fixture


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [
    None, "no_policy", "external", "other_role", "missing_selection",
    "disabled", "actor", "project", "agent", "other_selection", "stale",
    "expired", "future", "policy_actor",
])
async def test_script_activation_requires_exact_current_owned_selection(fault):
    context, definition = fixture()
    agent = context.agent
    selected = agent.device_script_selection
    policy = AgentActivationPolicy(
        template_id="assistant", channel="internal",
        updated_by=selected.configured_by,
    )
    policies = [policy]
    project, actor = selected.project_id, selected.configured_by
    if fault == "no_policy":
        policies = []
    elif fault == "external":
        policy.channel = "web"
    elif fault == "other_role":
        policy.template_id = "knowledge"
    elif fault == "missing_selection":
        agent.device_script_selection = None
    elif fault == "disabled":
        agent.device_script_selection = selected.model_copy(
            update={"enabled": False}
        )
    elif fault == "actor":
        actor = uuid4()
    elif fault == "policy_actor":
        policy.updated_by = uuid4()
    elif fault == "project":
        project = uuid4()
    elif fault == "agent":
        agent.id = uuid4()
    elif fault == "other_selection":
        definition = definition.model_copy(update={
            "selection": selected.model_copy(update={"revision": 2})
        })
    elif fault in {"stale", "expired"}:
        now = datetime.now(UTC)
        definition = definition.model_copy(update={
            "checked_at": now - timedelta(seconds=60),
            "valid_until": now - timedelta(seconds=30),
        })
    elif fault == "future":
        definition = definition.model_copy(update={
            "checked_at": datetime.now(UTC) + timedelta(seconds=10),
            "valid_until": datetime.now(UTC) + timedelta(seconds=20),
        })
    reader = AsyncMock(return_value=definition)
    if fault in {"no_policy", "external", "other_role"}:
        assert await script_activation_evidence(
            project, actor, agent, policies, reader
        ) is None
        reader.assert_not_awaited()
    elif fault is None:
        assert await script_activation_evidence(
            project, actor, agent, policies, reader
        ) == definition
    else:
        with pytest.raises(ConflictError):
            await script_activation_evidence(
                project, actor, agent, policies, reader
            )
