"""Formal authority derives the actor from the current channel policy and rechecks it."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.exceptions import AuthorizationError, ConflictError
from app.schemas.agent_business_execution import BusinessExecutionCheck
from app.services import agent_business_execution as module
from tests.test_agent_business_execution_protocol import command
from tests.test_agent_activation_business_evidence import business_setup
from tests.test_business_execution_context import fixture
from tests.test_agent_activation_readiness import setup as setup

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", [None, "knowledge_policy", "binding", "review", "customer", "late_change"]
)
async def test_only_exact_current_policy_review_customer_and_binding_can_query(
    setup, monkeypatch, fault
):
    db, agent, actor, binding, coverage, _ = await business_setup(setup)
    value = command()
    context, principal = fixture()
    context = context.model_copy(update={"project_id": agent.project_id})
    principal = principal.model_copy(update={"project_id": agent.project_id})
    principal.bindings[0] = principal.bindings[0].model_copy(update={"connection_id": binding.connection_id})
    value = value.model_copy(
        update={
            "reference": value.reference.model_copy(
                update={"project_id": agent.project_id, "agent_id": agent.id}
            ),
            "context": context,
            "connection_id": binding.connection_id,
            "binding_id": binding.id,
            "context_digest": principal.context_digest,
        }
    )
    agent.is_active = True
    await db.commit()
    reads = AsyncMock(return_value=value.reference)
    monkeypatch.setattr(module, "read_reference", reads)
    monkeypatch.setattr(
        module.api_service_client,
        "get_business_request_context",
        AsyncMock(return_value=principal),
    )
    if fault == "knowledge_policy":
        policy = setup[3]
        policy.template_id = "knowledge"
        await db.commit()
    elif fault == "binding":
        value = value.model_copy(update={"binding_revision": 9})
    elif fault == "review":
        coverage.capabilities[0].status = "rejected"
    elif fault == "customer":
        principal.bindings[0] = principal.bindings[0].model_copy(update={"connection_configuration_revision": 9})
    elif fault == "late_change":
        reads.side_effect = [
            value.reference,
            value.reference.model_copy(update={"policy_revision": "f" * 64}),
        ]
    owner = SimpleNamespace(
        configuration_revision=1,
        revision=3,
        authorization_revision=1,
        resource_revision="a" * 64,
        configuration_tests=[
            SimpleNamespace(
                operation="order_query",
                report_id=uuid4(),
                valid_until=principal.expires_at,
            )
        ],
    )
    trials = SimpleNamespace(
        _current=AsyncMock(
            return_value=(binding, owner, value.reference.configuration_fingerprint)
        )
    )
    capabilities = SimpleNamespace(read=AsyncMock(return_value=coverage))
    service = module.AgentBusinessExecutionService(
        db, trials=trials, capabilities=capabilities
    )
    check = BusinessExecutionCheck(command=value, phase="before_query")
    if fault:
        with pytest.raises((AuthorizationError, ConflictError)):
            await service.authorize(check)
    else:
        result = await service.authorize(check)
        assert (
            result.actor_id == actor
            and result.matches(value)
            and result.can_enable is False
        )
        assert (
            result.expires_at <= principal.expires_at
            and result.checked_at <= datetime.now(UTC)
        )
        assert trials._current.await_args.args[1] == actor
        assert trials._current.await_args.kwargs == {"require_paused": False}
