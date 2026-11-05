"""Workflow stages share quota and never regenerate on uncertain retries."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.models.ai_usage import AIUsageMovement, AIUsageReservation
from app.schemas.workflow_ai_usage import (
    WorkflowAICommand,
    WorkflowAIContext,
    WorkflowAIResult,
    WorkflowUsageOutcome,
    WorkflowUsageOwner,
)
from app.services import ai_usage, workflow_ai_usage as usage
from tests.test_ai_usage import prepare
from tests.test_billing_quotes import commercial_company  # noqa: F401


@pytest.fixture
def workflow_company(request, monkeypatch):
    db, staff, batch = prepare(request.getfixturevalue("commercial_company"))

    @contextmanager
    def session():
        yield db

    monkeypatch.setattr(usage, "SessionLocal", session)
    monkeypatch.setattr(settings, "SAAS_ENABLED", True)
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", True)
    return db, staff, batch


def call_for(staff, node="answer", execution=None, owner=None):
    command = WorkflowAICommand(
        project_id=staff.project_id,
        execution_id=execution or uuid4(),
        node_id=node,
        payload={"agent_id": uuid4(), "message": "四大服务是什么？"},
    )
    context = WorkflowAIContext(
        project_id=staff.project_id,
        execution_id=command.execution_id,
        node_id=node,
        agent_id=command.payload.agent_id,
        status="running",
        owner=owner
        or WorkflowUsageOwner(
            staff_id=staff.id,
            staff_token_version=staff.token_version,
        ),
    )
    return command, context


def result():
    return WorkflowAIResult(
        success=True, content="已根据当前资料回答。", execution_id=uuid4()
    )


def test_two_nodes_and_replay_share_one_charge(workflow_company):
    db, staff, batch = workflow_company
    command, context = call_for(staff)
    first = usage.claim(command, context)
    answer = result()
    usage.complete(first, answer)
    replay = usage.claim(command, context)
    assert replay.replay_execution_id == answer.execution_id
    second_command, second_context = call_for(
        staff, "followup", command.execution_id
    )
    second = usage.claim(second_command, second_context)
    assert second.permit == first.permit
    usage.complete(second, result())
    usage.finish(
        first.permit,
        WorkflowUsageOutcome(
            project_id=staff.project_id,
            execution_id=command.execution_id,
            status="completed",
            output="完整答复",
        ),
    )
    usage.finish(
        first.permit,
        WorkflowUsageOutcome(
            project_id=staff.project_id,
            execution_id=command.execution_id,
            status="completed",
            output="完整答复",
        ),
    )
    assert batch.remaining == 0
    assert db.query(AIUsageReservation).one().status == "settled"
    assert db.query(AIUsageMovement).count() == 2


@pytest.mark.parametrize("change", ["message", "agent", "device"])
def test_same_node_cannot_reuse_key_for_different_input(
    workflow_company, change
):
    _, staff, _ = workflow_company
    command, context = call_for(staff)
    usage.complete(usage.claim(command, context), result())
    payload = command.payload.model_dump()
    if change == "message":
        payload["message"] = "换一个问题"
    elif change == "agent":
        payload["agent_id"] = uuid4()
        context = context.model_copy(update={"agent_id": payload["agent_id"]})
    else:
        payload.update(
            expected_device_id=uuid4(), expected_device_policy_digest="a" * 64
        )
        context = context.model_copy(
            update={
                "expected_device_id": payload["expected_device_id"],
                "expected_device_policy_digest": "a" * 64,
            }
        )
    altered = command.model_copy(
        update={"payload": type(command.payload)(**payload)}
    )
    with pytest.raises(TGOAPIException) as error:
        usage.claim(altered, context)
    assert error.value.status_code == 409


@pytest.mark.parametrize(
    "field", ["project_id", "execution_id", "agent_id", "node_id"]
)
def test_unmatched_owned_context_never_reserves(workflow_company, field):
    db, staff, batch = workflow_company
    command, context = call_for(staff)
    context = context.model_copy(
        update={field: "other" if field == "node_id" else uuid4()}
    )
    with pytest.raises(TGOAPIException) as error:
        usage.claim(command, context)
    assert error.value.status_code == 403
    assert db.query(AIUsageReservation).count() == 0 and batch.remaining == 1


@pytest.mark.parametrize(
    "disabled", ["account_enabled", "is_active", "token_version", "role"]
)
def test_current_staff_eligibility_is_rechecked(workflow_company, disabled):
    db, staff, _ = workflow_company
    command, context = call_for(staff)
    setattr(
        staff,
        disabled,
        {"token_version": staff.token_version + 1, "role": "agent"}.get(
            disabled, False
        ),
    )
    db.commit()
    with pytest.raises(TGOAPIException) as error:
        usage.claim(command, context)
    assert error.value.status_code == 403
    assert db.query(AIUsageReservation).count() == 0


@pytest.mark.parametrize("state", ["claimed", "unknown"])
def test_unknown_or_concurrent_call_never_dispatches_twice(
    workflow_company, state
):
    db, staff, batch = workflow_company
    command, context = call_for(staff)
    first = usage.claim(command, context)
    if state == "unknown":
        usage.unknown(first)
    with pytest.raises(TGOAPIException) as error:
        usage.claim(command, context)
    assert error.value.status_code == 409
    usage.finish(
        first.permit,
        WorkflowUsageOutcome(
            project_id=staff.project_id,
            execution_id=command.execution_id,
            status="failed",
        ),
    )
    assert batch.remaining == 0
    assert db.query(AIUsageReservation).one().status == "review"


def test_rejected_generation_refunds_once(workflow_company):
    db, staff, batch = workflow_company
    command, context = call_for(staff)
    first = usage.claim(command, context)
    usage.reject(first)
    outcome = WorkflowUsageOutcome(
        project_id=staff.project_id,
        execution_id=command.execution_id,
        status="failed",
    )
    usage.finish(first.permit, outcome)
    usage.finish(first.permit, outcome)
    assert batch.remaining == 1
    assert db.query(AIUsageReservation).one().status == "released"
    assert db.query(AIUsageMovement).count() == 2


def test_customer_reply_lends_permit_without_new_charge_or_settlement(
    workflow_company,
):
    db, staff, batch = workflow_company
    parent = ai_usage.reserve(db, staff.project_id, "reply:original")
    parent.receipt = {"kind": "customer_turn", "turn_id": str(uuid4())}
    db.commit()
    owner = WorkflowUsageOwner(
        reservation_id=parent.id, lease_id=parent.lease_id
    )
    command, context = call_for(staff, owner=owner)
    claim = usage.claim(command, context)
    usage.complete(claim, result())
    usage.finish(
        claim.permit,
        WorkflowUsageOutcome(
            project_id=staff.project_id,
            execution_id=command.execution_id,
            status="completed",
            output="答复",
        ),
    )
    assert batch.remaining == 0
    assert db.query(AIUsageReservation).count() == 1
    assert (
        parent.status == "reserved"
        and parent.receipt["kind"] == "customer_turn"
    )
    assert db.query(AIUsageMovement).count() == 1


@pytest.mark.parametrize(
    "invalid", ["expired", "old_lease", "other_project", "settled"]
)
def test_invalid_inherited_permit_never_borrows_or_reserves(
    workflow_company, invalid
):
    db, staff, batch = workflow_company
    parent = ai_usage.reserve(db, staff.project_id, "reply:original")
    parent.receipt = {"kind": "im"}
    lease = parent.lease_id
    if invalid == "expired":
        parent.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    elif invalid == "old_lease":
        lease = uuid4()
    elif invalid == "other_project":
        parent.project_id = uuid4()
    else:
        parent.status = "settled"
    db.commit()
    command, context = call_for(
        staff,
        owner=WorkflowUsageOwner(reservation_id=parent.id, lease_id=lease),
    )
    with pytest.raises(TGOAPIException):
        usage.claim(command, context)
    assert batch.remaining == 0 and db.query(AIUsageReservation).count() == 1


def test_late_reply_cannot_overwrite_new_lease(workflow_company):
    db, staff, _ = workflow_company
    command, context = call_for(staff)
    original = usage.claim(command, context)
    row = db.query(AIUsageReservation).one()
    row.lease_id = uuid4()
    db.commit()
    with pytest.raises(TGOAPIException):
        usage.complete(original, result())


@pytest.mark.parametrize("available", [False, True])
def test_cancelled_run_bills_only_an_answer_available_in_history(
    workflow_company,
    available,
):
    db, staff, batch = workflow_company
    command, context = call_for(staff)
    claimed = usage.claim(command, context)
    usage.complete(claimed, result())
    usage.finish(
        claimed.permit,
        WorkflowUsageOutcome(
            project_id=staff.project_id,
            execution_id=command.execution_id,
            status="cancelled",
            answer_available=available,
        ),
    )
    assert batch.remaining == (0 if available else 1)
    assert db.query(AIUsageReservation).one().status == (
        "settled" if available else "released"
    )


def test_corrupt_receipt_does_not_refund_or_repeat(workflow_company):
    db, staff, batch = workflow_company
    command, context = call_for(staff)
    original = usage.claim(command, context)
    row = db.query(AIUsageReservation).one()
    row.receipt = {**row.receipt, "workflow_ai_calls": {"bad": "corrupt"}}
    db.commit()
    with pytest.raises(TGOAPIException):
        usage.finish(
            original.permit,
            WorkflowUsageOutcome(
                project_id=staff.project_id,
                execution_id=command.execution_id,
                status="failed",
            ),
        )
    assert batch.remaining == 0


def test_plaintext_question_and_service_credentials_are_not_in_receipt(
    workflow_company,
):
    db, staff, _ = workflow_company
    command, context = call_for(staff)
    usage.claim(command, context)
    receipt = db.query(AIUsageReservation).one().receipt
    assert command.payload.message not in str(receipt)
    assert settings.SECRET_KEY not in str(receipt)


def test_billed_company_cannot_request_unmetered_workflow(workflow_company):
    db, staff, batch = workflow_company
    command, context = call_for(
        staff, owner=WorkflowUsageOwner(unmetered=True)
    )
    with pytest.raises(TGOAPIException) as error:
        usage.claim(command, context)
    assert error.value.status_code == 403 and batch.remaining == 1
    assert db.query(AIUsageReservation).count() == 0


def test_actual_model_gate_rechecks_staff_after_claim(workflow_company):
    db, staff, _ = workflow_company
    command, context = call_for(staff)
    usage.claim(command, context)
    staff.account_enabled = False
    db.commit()
    with pytest.raises(TGOAPIException) as error:
        usage.require_current_owner(db, db.query(AIUsageReservation).one())
    assert error.value.status_code == 403


def test_approval_resume_refreshes_lease_without_another_charge(
    workflow_company,
):
    db, staff, batch = workflow_company
    command, context = call_for(staff)
    first = usage.claim(command, context)
    usage.complete(first, result())
    usage.finish(
        first.permit,
        WorkflowUsageOutcome(
            project_id=staff.project_id,
            execution_id=command.execution_id,
            status="waiting_approval",
        ),
    )
    assert db.query(AIUsageReservation).one().status == "review"
    resumed_command, resumed_context = call_for(
        staff, "resumed", command.execution_id
    )
    resumed = usage.claim(resumed_command, resumed_context)
    assert resumed.permit.reservation_id == first.permit.reservation_id
    assert resumed.permit.lease_id != first.permit.lease_id
    assert batch.remaining == 0 and db.query(AIUsageMovement).count() == 1
    with pytest.raises(TGOAPIException):
        usage.unknown(first)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["completed", "failed", "unavailable"])
async def test_recovery_reads_original_workflow_without_regeneration(
    workflow_company, monkeypatch, outcome
):
    from app.services import ai_usage_recovery, workflow_ai_recovery
    from fastapi import HTTPException
    from unittest.mock import AsyncMock

    db, staff, batch = workflow_company
    command, context = call_for(staff)
    first = usage.claim(command, context)
    usage.complete(first, result())
    row = db.query(AIUsageReservation).one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    monkeypatch.setattr(ai_usage_recovery, "SessionLocal", usage.SessionLocal)
    monkeypatch.setattr(
        workflow_ai_recovery, "SessionLocal", usage.SessionLocal
    )

    async def immediate(function, *args):
        return function(*args)

    monkeypatch.setattr(workflow_ai_recovery.asyncio, "to_thread", immediate)
    ai_usage_recovery.release_abandoned()
    assert row.status == "reserved" and batch.remaining == 0
    read = AsyncMock(
        return_value=WorkflowUsageOutcome(
            project_id=staff.project_id,
            execution_id=command.execution_id,
            status="completed" if outcome == "completed" else "failed",
            output="答复",
        )
    )
    if outcome == "unavailable":
        read.side_effect = HTTPException(503, "synthetic")
    monkeypatch.setattr(
        workflow_ai_recovery.workflow_client, "workflow_usage_outcome", read
    )
    await workflow_ai_recovery.reconcile_workflows()
    assert (
        row.status
        == {
            "completed": "settled",
            "failed": "released",
            "unavailable": "review",
        }[outcome]
    )
    assert batch.remaining == (1 if outcome == "failed" else 0)
    assert read.await_args.args == (staff.project_id, command.execution_id)
