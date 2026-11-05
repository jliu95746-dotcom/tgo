"""Answer recovery reads the original result and never starts another model."""
from unittest.mock import AsyncMock
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.core.exceptions import TGOAPIException
from app.schemas.agent_execution import (
    AgentExecutionCheckReceipt,
    AgentExecutionReference,
)
from app.schemas.workflow_ai_usage import (
    WorkflowAICacheCommand,
    WorkflowAICacheReceipt,
)
from app.services import workflow_ai_dispatch as dispatch
from app.services import workflow_ai_usage as usage
from tests.test_workflow_ai_dispatch import (
    immediate_database_calls,
)  # noqa: F401
from tests.test_workflow_ai_usage import call_for, result
from tests.test_workflow_ai_usage import workflow_company  # noqa: F401
from tests.test_billing_quotes import commercial_company  # noqa: F401


@pytest.fixture
def prepared(workflow_company, monkeypatch):
    db, staff, batch = workflow_company
    command, context = call_for(staff)
    monkeypatch.setattr(
        dispatch.workflow_client,
        "workflow_ai_context",
        AsyncMock(return_value=context),
    )
    saved = {}

    async def cache(operation, request):
        key = (request.command.execution_id, request.command.node_id)
        base = WorkflowAICacheCommand(
            command=request.command,
            reservation_id=request.reservation_id,
            lease_id=request.lease_id,
        )
        if key not in saved:
            if operation != "prepare":
                raise HTTPException(404, "No original result")
            saved[key] = (
                base,
                WorkflowAICacheReceipt(state="pending", claim_id=uuid4()),
            )
            return saved[key][1].model_copy(update={"state": "claimed"})
        original, receipt = saved[key]
        if original != base:
            raise HTTPException(409, "Different original scope")
        if operation == "store":
            assert request.claim_id == receipt.claim_id
            receipt = WorkflowAICacheReceipt(
                state="completed",
                claim_id=receipt.claim_id,
                result=request.result,
            )
            saved[key] = (original, receipt)
        return receipt

    monkeypatch.setattr(dispatch.workflow_client, "workflow_ai_result", cache)
    generate = AsyncMock(return_value=result())
    reference = AgentExecutionReference(
        project_id=command.project_id,
        agent_id=command.payload.agent_id,
        channel="internal",
        configuration_fingerprint="a" * 64,
        policy_revision="b" * 64,
        business_evidence_revision="c" * 64,
    )
    generate.return_value = generate.return_value.model_copy(
        update={
            "execution_reference": reference,
        }
    )
    verify = AsyncMock(
        side_effect=lambda ref: AgentExecutionCheckReceipt(
            reference=ref,
            checked_at=datetime.now(UTC),
        )
    )
    monkeypatch.setattr(dispatch.ai_client, "verify_agent_execution", verify)
    monkeypatch.setattr(dispatch.ai_client, "run_workflow_agent", generate)
    return db, staff, batch, command, generate, cache


@pytest.mark.asyncio
async def test_completed_original_call_returns_original_answer(prepared):
    db, _, batch, command, generate, _ = prepared
    original = await dispatch.run(command)
    assert await dispatch.run(command) == original
    assert await dispatch.recover(command) == original
    assert generate.await_count == 1 and batch.remaining == 0
    assert db.query(usage.AIUsageReservation).count() == 1


@pytest.mark.asyncio
async def test_lost_cache_ack_reads_result_without_regenerating(
    prepared, monkeypatch
):
    db, _, _, command, generate, cache = prepared

    async def lost_ack(operation, request):
        response = await cache(operation, request)
        if operation == "store":
            raise HTTPException(503, "synthetic lost storage acknowledgement")
        return response

    monkeypatch.setattr(
        dispatch.workflow_client, "workflow_ai_result", lost_ack
    )
    with pytest.raises(HTTPException):
        await dispatch.run(command)
    assert await dispatch.recover(command) == generate.return_value
    assert generate.await_count == 1
    assert (
        next(
            iter(
                db.query(usage.AIUsageReservation)
                .one()
                .receipt["workflow_ai_calls"]
                .values()
            )
        )["state"]
        == "completed"
    )


@pytest.mark.asyncio
async def test_recovery_rechecks_original_configuration_not_a_new_reference(
    prepared,
):
    _, _, _, command, generate, _ = prepared
    original = await dispatch.run(command)
    dispatch.ai_client.verify_agent_execution.reset_mock()
    assert await dispatch.recover(command) == original
    dispatch.ai_client.verify_agent_execution.assert_awaited_once_with(
        generate.return_value.execution_reference,
    )
    assert generate.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 409, 503])
async def test_revoked_or_unavailable_original_knowledge_denies_recovery(
    prepared, status
):
    db, _, _, command, generate, _ = prepared
    await dispatch.run(command)
    dispatch.ai_client.verify_agent_execution.side_effect = HTTPException(
        status,
        "synthetic permission or knowledge revision changed",
    )
    with pytest.raises(HTTPException):
        await dispatch.recover(command)
    assert generate.await_count == 1
    assert db.query(usage.AIUsageReservation).count() == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing", "project", "agent", "channel"])
async def test_unproven_producer_is_never_returned(prepared, fault):
    _, _, _, command, generate, _ = prepared
    ref = generate.return_value.execution_reference
    if fault == "missing":
        ref = None
    else:
        ref = ref.model_copy(
            update={
                "channel"
                if fault == "channel"
                else fault + "_id": "web"
                if fault == "channel"
                else uuid4(),
            }
        )
    generate.return_value = generate.return_value.model_copy(
        update={"execution_reference": ref}
    )
    with pytest.raises(HTTPException):
        await dispatch.run(command)
    dispatch.ai_client.verify_agent_execution.assert_not_awaited()
    assert generate.await_count == 1


@pytest.mark.asyncio
async def test_unknown_model_result_is_not_regenerated(prepared):
    db, _, _, command, generate, _ = prepared
    generate.side_effect = HTTPException(504, "private provider error")
    with pytest.raises(HTTPException):
        await dispatch.run(command)
    with pytest.raises(HTTPException):
        await dispatch.recover(command)
    assert generate.await_count == 1
    assert db.query(usage.AIUsageReservation).one().status == "review"


@pytest.mark.asyncio
async def test_recovery_without_an_original_call_does_not_reserve(prepared):
    db, _, batch, command, generate, _ = prepared
    with pytest.raises((HTTPException, TGOAPIException)):
        await dispatch.recover(command)
    assert db.query(usage.AIUsageReservation).count() == 0
    assert batch.remaining == 1
    generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_revoked_staff_cannot_retrieve_cached_answer(prepared):
    db, staff, _, command, generate, _ = prepared
    await dispatch.run(command)
    staff.token_version += 1
    db.commit()
    with pytest.raises((HTTPException, TGOAPIException)):
        await dispatch.recover(command)
    assert generate.await_count == 1


@pytest.mark.asyncio
async def test_changed_question_cannot_read_or_restart_original_call(prepared):
    _, _, _, command, generate, _ = prepared
    await dispatch.run(command)
    changed = command.model_copy(
        update={
            "payload": command.payload.model_copy(
                update={"message": "different question"}
            )
        }
    )
    with pytest.raises((HTTPException, TGOAPIException)):
        await dispatch.recover(changed)
    assert generate.await_count == 1


@pytest.mark.asyncio
async def test_mismatched_storage_ack_never_returns_substituted_result(
    prepared, monkeypatch
):
    _, _, _, command, generate, cache = prepared

    async def mismatched_ack(operation, request):
        receipt = await cache(operation, request)
        if operation == "store":
            return receipt.model_copy(update={"claim_id": uuid4()})
        return receipt

    monkeypatch.setattr(
        dispatch.workflow_client, "workflow_ai_result", mismatched_ack
    )
    with pytest.raises(HTTPException) as error:
        await dispatch.run(command)
    assert error.value.status_code == 503
    assert await dispatch.recover(command) == generate.return_value
    assert generate.await_count == 1
