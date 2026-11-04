"""Source approvals continue exact frozen inputs and revoke before decryption."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

from pydantic import SecretStr
import pytest
from sqlalchemy import select

from app.config import settings
from app.models.approval import WorkflowApprovalRequest
from app.models.execution import NodeExecution, WorkflowExecution
from app.models.workflow import Workflow
from app.models.workflow_release import WorkflowRelease
from app.models.workflow_webhook import WorkflowWebhook
from app.models.workflow_webhook_delivery import WorkflowWebhookDelivery
from app.services import event_trigger, event_source_audit, webhook_runtime
from app.services.approval_state import decide_approval
from app.services.execution_snapshot import copy_json_object, definition_digest
from app.services.validation_service import ValidationService
from celery_app import tasks
from tests.unit.test_event_source_binding import (
    binding,
    request_for,
    source_store as _event_store,
)
from tests.unit.test_webhook_ingress import fixture as _webhook_store
from tests.unit.test_webhook_dispatch import queued
from tests.unit.test_event_source_audit import Progress

source_store = _event_store
fixture = _webhook_store


def with_approval(graph, actor, reference):
    result = copy_json_object(graph)
    trigger, answer = result["nodes"]
    approval = {
        "id": "approval",
        "type": "approval",
        "data": {
            "type": "approval",
            "reference_key": "approval",
            "label": "人工确认",
            "approver_staff_id": actor,
            "summary": "确认本次来源数据 {{" + reference + "}}",
            "expires_in_minutes": 60,
        },
    }
    result["nodes"] = [trigger, approval, answer]
    result["edges"] = [
        {"source": trigger["id"], "target": "approval"},
        {"source": "approval", "target": answer["id"]},
    ]
    return result


@pytest.mark.parametrize("source", ["event", "webhook"])
def test_source_plain_confirmation_is_valid_but_customer_action_is_not(
    source, monkeypatch
):
    monkeypatch.setattr(settings, "WEBHOOK_EXECUTION_ENABLED", True)
    actor = str(uuid4())
    graph = with_approval(
        {
            "nodes": [
                {
                    "id": "source",
                    "type": source,
                    "data": {
                        "reference_key": "source",
                        "event_type": "qa.received",
                        "method": "POST",
                    },
                },
                {
                    "id": "answer",
                    "type": "answer",
                    "data": {
                        "reference_key": "answer",
                        "output_type": "template",
                        "output_template": "完成",
                    },
                },
            ],
            "edges": [],
        },
        actor,
        "source.data" if source == "event" else "source.body",
    )
    assert not ValidationService.validate_workflow(
        graph, allow_webhook_preparation=True
    )
    graph["nodes"][1]["data"].update(
        after_approval_action="create_aftersales_review", case_category="return_request"
    )
    assert any(
        "可信客户会话" in error
        for error in ValidationService.validate_workflow(
            graph, allow_webhook_preparation=True
        )
    )


@pytest.mark.asyncio
async def test_source_event_pauses_and_resumes_exact_data_once(
    source_store, monkeypatch
):
    sessions, project, workflow, _context, _verify = source_store
    actor = str(uuid4())
    async with sessions() as db:
        connection = await db.connection()
        await connection.run_sync(NodeExecution.__table__.create)
        flow = await db.get(Workflow, workflow)
        graph = with_approval(flow.definition, actor, "event.data.summary")
        graph["nodes"][-1]["data"].update(
            output_type="variable", output_variable="event.data"
        )
        release = await db.scalar(
            select(WorkflowRelease).where(WorkflowRelease.workflow_id == workflow)
        )
        release.definition, release.definition_digest = graph, definition_digest(graph)
        flow.definition = graph
        await db.commit()
        context = await event_trigger.event_test_context(db, workflow, project)
        source = binding()
        accepted = await event_trigger.accept_event(
            db,
            workflow,
            request_for(project, context, source),
            "source-" + str(source.delivery_id),
            "synthetic-http-key",
        )
    monkeypatch.setattr(tasks, "workflow_task_session", sessions)
    monkeypatch.setattr(tasks, "verify_approval_assignees", AsyncMock())
    await tasks.async_execute_workflow(accepted.execution_id, workflow, {}, project)
    async with sessions() as db:
        run = await db.get(WorkflowExecution, accepted.execution_id)
        assert run.status == "waiting_approval"
        approval = await db.get(WorkflowApprovalRequest, run.active_approval_id)
        assert approval.checkpoint["capture_status"] == "encrypted"
        await approve(db, project, approval, actor)
    for _ in range(2):
        await tasks.async_execute_workflow(
            accepted.execution_id, workflow, {"data": {"summary": "FORGED"}}, project
        )
    async with sessions() as db:
        run = await db.get(WorkflowExecution, accepted.execution_id)
        assert run.status == "completed" and run.input is None
        assert run.output == {"result": {"summary": "private content"}}
        assert len(list(await db.scalars(select(NodeExecution)))) == 3


async def approve(db, project, approval, actor):
    from app.services.approval_preview import approval_preview_digest

    await decide_approval(
        db,
        project,
        approval.id,
        actor,
        "approve",
        expected_preview_digest=approval_preview_digest(
            approval.id, approval.execution_id, approval.summary
        ),
    )


@asynccontextmanager
async def waiting_webhook(fixture, monkeypatch):
    async with fixture.sessions() as db:
        flow = await db.get(Workflow, fixture.workflow_id)
        release = await db.get(WorkflowRelease, fixture.release_id)
        graph = with_approval(
            release.definition, str(fixture.actor_id), "source.body.question"
        )
        digest = definition_digest(graph)
        release.definition, release.definition_digest = graph, digest
        hook = await db.get(WorkflowWebhook, str(fixture.hook_id))
        hook.release_digest, flow.definition = digest, graph
        fixture.digest = digest
        await db.commit()
    identifier, delivery = await queued(fixture, monkeypatch)
    monkeypatch.setattr(tasks, "verify_approval_assignees", AsyncMock())
    await tasks.async_execute_workflow(
        identifier, fixture.workflow_id, {}, str(fixture.project_id)
    )
    async with fixture.sessions() as db:
        run = await db.get(WorkflowExecution, identifier)
        assert run.status == "waiting_approval"
        approval = await db.get(WorkflowApprovalRequest, run.active_approval_id)
        assert approval.checkpoint["capture_status"] == "encrypted"
        approval_id = approval.id
    yield identifier, str(delivery), approval_id


@pytest.mark.asyncio
async def test_webhook_confirmation_resumes_original_payload_once(fixture, monkeypatch):
    async with waiting_webhook(fixture, monkeypatch) as (
        identifier,
        delivery,
        approval_id,
    ):
        async with fixture.sessions() as db:
            approval = await db.get(WorkflowApprovalRequest, approval_id)
            await approve(db, str(fixture.project_id), approval, str(fixture.actor_id))
        for _ in range(2):
            await tasks.async_execute_workflow(
                identifier,
                fixture.workflow_id,
                {"data": {"question": "FORGED"}},
                str(fixture.project_id),
            )
        async with fixture.sessions() as db:
            run = await db.get(WorkflowExecution, identifier)
            source = await db.get(WorkflowWebhookDelivery, delivery)
            assert run.status == "completed" and run.input is None
            assert run.output == {"result": "synthetic-private-question"}
            assert source.status == "completed" and source.payload_ciphertext is None
            assert len(list(await db.scalars(select(NodeExecution)))) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["waiting_approval", "pending_resume"])
@pytest.mark.parametrize("change", ["disable", "rebind", "authority"])
async def test_webhook_revocation_precedes_missing_checkpoint_key(
    fixture, monkeypatch, state, change
):
    async with waiting_webhook(fixture, monkeypatch) as (
        identifier,
        delivery,
        approval_id,
    ):
        async with fixture.sessions() as db:
            if state == "pending_resume":
                approval = await db.get(WorkflowApprovalRequest, approval_id)
                await approve(
                    db, str(fixture.project_id), approval, str(fixture.actor_id)
                )
            hook = await db.get(WorkflowWebhook, str(fixture.hook_id))
            if change == "disable":
                hook.enabled = False
            elif change == "rebind":
                hook.revision += 1
            await db.commit()
        if change == "authority":
            monkeypatch.setattr(
                webhook_runtime,
                "verify_webhook_authority",
                AsyncMock(side_effect=PermissionError()),
            )
        monkeypatch.setattr(
            settings,
            "WORKFLOW_APPROVAL_KEYS",
            SecretStr('{"absent":"' + "n" * 48 + '"}'),
        )
        monkeypatch.setattr(settings, "WORKFLOW_APPROVAL_ACTIVE_KEY_ID", "absent")
        monkeypatch.setattr(settings, "WORKFLOW_WEBHOOK_KEYS", None)
        if state == "waiting_approval":
            assert (
                await event_source_audit.audit_waiting_event_sources(
                    fixture.sessions, Progress()
                )
                == 1
            )
        else:
            await tasks.async_execute_workflow(
                identifier, fixture.workflow_id, {}, str(fixture.project_id)
            )
        async with fixture.sessions() as db:
            run = await db.get(WorkflowExecution, identifier)
            approval = await db.get(WorkflowApprovalRequest, approval_id)
            source = await db.get(WorkflowWebhookDelivery, delivery)
            assert run.status == "cancelled" and run.input is None
            assert source.payload_ciphertext is None and source.status == "cancelled"
            assert approval.status == (
                "approved" if state == "pending_resume" else "cancelled"
            )
            assert approval.checkpoint["capture_status"] == "redacted"
            assert len(list(await db.scalars(select(NodeExecution)))) == 2


@pytest.mark.asyncio
async def test_waiting_webhook_audit_defers_unknown_authority_without_decryption(
    fixture, monkeypatch
):
    async with waiting_webhook(fixture, monkeypatch) as (
        identifier,
        delivery,
        approval_id,
    ):
        async with fixture.sessions() as db:
            original = dict(
                (await db.get(WorkflowApprovalRequest, approval_id)).checkpoint
            )
            body = (await db.get(WorkflowWebhookDelivery, delivery)).payload_ciphertext
        monkeypatch.setattr(settings, "WORKFLOW_APPROVAL_KEYS", None)
        monkeypatch.setattr(settings, "WORKFLOW_WEBHOOK_KEYS", None)
        verify = AsyncMock(side_effect=RuntimeError("private-service-unavailable"))
        monkeypatch.setattr(webhook_runtime, "verify_webhook_authority", verify)
        progress = Progress()
        assert (
            await event_source_audit.audit_waiting_event_sources(
                fixture.sessions, progress
            )
            == 0
        )
        async with fixture.sessions() as db:
            assert (
                await db.get(WorkflowExecution, identifier)
            ).status == "waiting_approval"
            assert (
                await db.get(WorkflowApprovalRequest, approval_id)
            ).checkpoint == original
            assert (
                await db.get(WorkflowWebhookDelivery, delivery)
            ).payload_ciphertext == body
        verify.side_effect = PermissionError("revoked-after-outage")
        assert (
            await event_source_audit.audit_waiting_event_sources(
                fixture.sessions, progress
            )
            == 1
        )


@pytest.mark.asyncio
async def test_webhook_preclaim_checks_authority_without_claiming_the_payload(
    fixture, monkeypatch
):
    identifier, delivery = await queued(fixture, monkeypatch)
    async with fixture.sessions() as db:
        await webhook_runtime.require_active_webhook(
            db,
            str(fixture.project_id),
            fixture.workflow_id,
            identifier,
            expected_status="pending",
        )
    async with fixture.sessions() as db:
        assert (await db.get(WorkflowExecution, identifier)).status == "pending"
        assert (await db.get(WorkflowWebhookDelivery, str(delivery))).claimed_at is None


@pytest.mark.asyncio
async def test_stale_wait_audit_cannot_cancel_a_concurrent_webhook_approval(
    fixture, monkeypatch
):
    async with waiting_webhook(fixture, monkeypatch) as (
        identifier,
        delivery,
        approval_id,
    ):
        changed = False

        async def approve_then_revoke(_query):
            nonlocal changed
            if not changed:
                async with fixture.sessions() as db:
                    request = await db.get(WorkflowApprovalRequest, approval_id)
                    await approve(
                        db, str(fixture.project_id), request, str(fixture.actor_id)
                    )
                changed = True
            raise PermissionError("source-revoked")

        monkeypatch.setattr(
            webhook_runtime, "verify_webhook_authority", approve_then_revoke
        )
        progress = Progress()
        assert (
            await event_source_audit.audit_waiting_event_sources(
                fixture.sessions, progress
            )
            == 0
        )
        async with fixture.sessions() as db:
            assert (
                await db.get(WorkflowExecution, identifier)
            ).status == "pending_resume"
            request = await db.get(WorkflowApprovalRequest, approval_id)
            assert (
                request.status == "approved"
                and request.checkpoint["capture_status"] == "encrypted"
            )
        assert (
            await event_source_audit.audit_waiting_event_sources(
                fixture.sessions, progress
            )
            == 1
        )
        async with fixture.sessions() as db:
            assert (await db.get(WorkflowExecution, identifier)).status == "cancelled"
            assert (
                await db.get(WorkflowApprovalRequest, approval_id)
            ).status == "approved"
