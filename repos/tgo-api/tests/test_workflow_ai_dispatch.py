"""The private bridge binds quota after reading actual Workflow ownership."""
from unittest.mock import AsyncMock
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services import workflow_ai_dispatch as dispatch
from app.services import ai_usage_runtime
from app.services import workflow_ai_usage as usage
from app.schemas.workflow_ai_usage import WorkflowAICacheReceipt
from app.schemas.agent_execution import (
    AgentExecutionReference,
    AgentExecutionCheckReceipt,
)
from tests.test_workflow_ai_usage import call_for, result
from tests.test_workflow_ai_usage import workflow_company  # noqa: F401
from tests.test_billing_quotes import commercial_company  # noqa: F401


@pytest.fixture(autouse=True)
def immediate_database_calls(monkeypatch):
    async def immediate(function, *args):
        return function(*args)

    monkeypatch.setattr(dispatch.asyncio, "to_thread", immediate)


@pytest.fixture
def case(request):
    return request.getfixturevalue("workflow_company")


@pytest.fixture(autouse=True)
def saved_answer_transport(monkeypatch):
    receipts = {}

    async def cache(operation, request):
        key = (request.command.execution_id, request.command.node_id)
        if key not in receipts:
            receipts[key] = WorkflowAICacheReceipt(
                state="pending", claim_id=uuid4()
            )
            return receipts[key].model_copy(update={"state": "claimed"})
        if operation == "store":
            receipts[key] = WorkflowAICacheReceipt(
                state="completed",
                claim_id=request.claim_id,
                result=request.result,
            )
        return receipts[key]

    monkeypatch.setattr(dispatch.workflow_client, "workflow_ai_result", cache)


@pytest.mark.asyncio
async def test_dispatch_uses_exact_permit_and_single_agent(case, monkeypatch):
    _, staff, batch = case
    command, context = call_for(staff)
    answer = result()
    answer = answer.model_copy(
        update={
            "execution_reference": AgentExecutionReference(
                project_id=command.project_id,
                agent_id=command.payload.agent_id,
                channel="internal",
                configuration_fingerprint="a" * 64,
                policy_revision="b" * 64,
            )
        }
    )
    monkeypatch.setattr(
        dispatch.ai_client,
        "verify_agent_execution",
        AsyncMock(
            return_value=AgentExecutionCheckReceipt(
                reference=answer.execution_reference,
                checked_at=datetime.now(UTC),
            ),
        ),
    )
    generated = []
    monkeypatch.setattr(
        dispatch.workflow_client,
        "workflow_ai_context",
        AsyncMock(return_value=context),
    )

    async def generate(payload, project_id, execution_id, lineage):
        generated.append(payload)
        permit = ai_usage_runtime.current_permit()
        assert permit is not None and permit.project_id == staff.project_id
        assert (
            payload == command.payload and execution_id == command.execution_id
        )
        return answer

    monkeypatch.setattr(dispatch.ai_client, "run_workflow_agent", generate)
    assert await dispatch.run(command) == answer
    assert batch.remaining == 0 and ai_usage_runtime.current_permit() is None
    assert await dispatch.run(command) == answer
    assert len(generated) == 1


@pytest.mark.asyncio
async def test_ownership_change_after_claim_stops_before_model(
    case, monkeypatch
):
    db, staff, _ = case
    command, context = call_for(staff)
    monkeypatch.setattr(
        dispatch.workflow_client,
        "workflow_ai_context",
        AsyncMock(side_effect=[context, HTTPException(403, "stopped")]),
    )
    generate = AsyncMock()
    monkeypatch.setattr(dispatch.ai_client, "run_workflow_agent", generate)
    with pytest.raises(HTTPException):
        await dispatch.run(command)
    generate.assert_not_awaited()
    row = db.query(usage.AIUsageReservation).one()
    assert (
        next(iter(row.receipt["workflow_ai_calls"].values()))["state"]
        == "rejected"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 504])
async def test_rejection_and_unknown_have_distinct_receipts(
    case, monkeypatch, status
):
    db, staff, _ = case
    command, context = call_for(staff)
    monkeypatch.setattr(
        dispatch.workflow_client,
        "workflow_ai_context",
        AsyncMock(return_value=context),
    )
    generate = AsyncMock(
        side_effect=HTTPException(status, "private-upstream-message")
    )
    monkeypatch.setattr(dispatch.ai_client, "run_workflow_agent", generate)
    with pytest.raises(HTTPException) as error:
        await dispatch.run(command)
    assert "private-upstream-message" not in str(error.value.detail)
    row = db.query(usage.AIUsageReservation).one()
    expected = "rejected" if status == 403 else "unknown"
    assert (
        next(iter(row.receipt["workflow_ai_calls"].values()))["state"]
        == expected
    )
    with pytest.raises(Exception):
        await dispatch.run(command)
    assert generate.await_count == 1
