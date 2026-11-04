"""Exhausted capacity never waives another approval or scope condition."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models.agent_activation_policy import AgentActivationPolicy
from app.schemas.agent_activation_readiness import (
    ActivationBlocker,
    AgentActivationReadiness,
)
from app.services.agent_execution_observation import (
    allows_capacity_observation,
)
from app.services.agent_execution_reference import policy_revision
from app.services.agent_execution_scope import track_execution_scope
from tests.test_employee_device_script_tools import fixture


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [
    None, "outside_scope", "closed", "revoked", "expected", "policy",
    "configuration", "evidence", "other_blocker", "missing_blocker",
    "wrong_resource", "wrong_channel", "expired", "status", "role",
])
async def test_only_current_internal_approval_can_observe_exhausted_run(fault):
    context, _ = fixture()
    reference = context.execution_reference
    policy = AgentActivationPolicy(
        id=uuid4(), project_id=reference.project_id,
        agent_id=reference.agent_id, updated_by=uuid4(), created_by=uuid4(),
        template_id="assistant", template_version=1, channel="internal",
        revision=1, requirements_revision="e" * 64,
    )
    reference = reference.model_copy(update={
        "policy_revision": policy_revision([policy], reference.channel)
    })
    proof = AgentActivationReadiness(
        project_id=reference.project_id, agent_id=reference.agent_id,
        checked_by=policy.updated_by, checked_at=datetime.now(UTC),
        enabled=True, can_enable=False,
        configuration_fingerprint=reference.configuration_fingerprint,
        model_owner_revision="a" * 64,
        business_evidence_revision=reference.business_evidence_revision,
        blockers=[ActivationBlocker(
            code="script_capacity_exhausted", channel="internal",
            resource="device", message="次数已用完",
        )],
        channels=[{
            "template_id": "assistant", "template_version": 1,
            "channel": "internal", "policy_revision": 1,
            "requirements_revision": "e" * 64, "status": "passed",
            "valid_until": datetime.now(UTC) + timedelta(seconds=30),
        }],
    )
    expected = reference.model_copy(deep=True)
    if fault == "outside_scope":
        assert not allows_capacity_observation(proof, expected, [policy])
        return
    if fault == "expected":
        expected = None
    elif fault == "policy":
        policy.revision += 1
    elif fault == "configuration":
        proof.configuration_fingerprint = "f" * 64
    elif fault == "evidence":
        proof.business_evidence_revision = None
    elif fault == "other_blocker":
        proof.blockers.append(ActivationBlocker(
            code="tests_not_current", message="审批已变化"
        ))
    elif fault == "missing_blocker":
        proof.blockers = []
    elif fault == "wrong_resource":
        proof.blockers[0].resource = "model"
    elif fault == "wrong_channel":
        proof.channels[0].channel = "web"
    elif fault == "expired":
        proof.channels[0].valid_until = (
            datetime.now(UTC) - timedelta(seconds=1)
        )
    elif fault == "status":
        proof.channels[0].status = "needs_retest"
    elif fault == "role":
        proof.channels[0].template_id = "logistics"
    async with track_execution_scope(reference, AsyncMock()) as scope:
        if fault == "closed":
            scope.closed = True
        elif fault == "revoked":
            scope.revoked = True
        assert allows_capacity_observation(proof, expected, [policy]) is (
            fault is None
        )
