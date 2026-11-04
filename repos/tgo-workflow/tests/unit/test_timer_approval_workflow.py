"""Timer approval resumes only under its original authorized source."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.approval import WorkflowApprovalRequest
from app.models.execution import NodeExecution, WorkflowExecution
from app.models.workflow import Workflow
from app.models.workflow_release import WorkflowRelease
from app.models.workflow_schedule import WorkflowSchedule
from app.schemas.workflow_schedule import ScheduleDisable
from app.services import timer_dispatch, timer_runtime
from app.services import workflow_schedules
from app.services.approval_preview import approval_preview_digest
from app.services.approval_state import decide_approval
from app.services.execution_snapshot import copy_json_object, definition_digest
from app.services.validation_service import ValidationService
from celery_app import tasks
from tests.unit.test_timer_dispatch import due_plan
from tests.unit.test_workflow_schedules import schedule_database


def timer_graph(graph, approver):
    result = copy_json_object(graph)
    result["nodes"][0]["data"]["cron_expression"] = "* * * * *"
    result["nodes"][1]["data"]["output_template"] = (
        "审批后完成：{{clock.timestamp}}"
    )
    result["nodes"].insert(
        1,
        {
            "id": "approval",
            "type": "approval",
            "data": {
                "type": "approval",
                "reference_key": "approval",
                "label": "人工核对",
                "approver_staff_id": approver,
                "summary": "确认本次定时批次 {{clock.timestamp}}",
                "expires_in_minutes": 60,
            },
        },
    )
    result["edges"] = [
        {"source": "clock", "target": "approval"},
        {"source": "approval", "target": "answer"},
    ]
    return result


@asynccontextmanager
async def waiting_timer(monkeypatch):
    monkeypatch.setattr(settings, "TIMER_EXECUTION_ENABLED", True)
    verify_assignee = AsyncMock()
    monkeypatch.setattr(tasks, "verify_approval_assignees", verify_assignee)
    monkeypatch.setattr(timer_dispatch, "verify_timer_authority", AsyncMock())
    monkeypatch.setattr(timer_dispatch, "enqueue_pending_initial", AsyncMock())
    monkeypatch.setattr(timer_runtime, "verify_timer_authority", AsyncMock())
    async with schedule_database() as h:
        async with h.sessions() as db:
            release = await db.get(WorkflowRelease, h.release_id)
            graph = timer_graph(release.definition, str(h.actor_id))
            h.digest = definition_digest(graph)
            release.definition = graph
            release.definition_digest = h.digest
            flow = await db.get(Workflow, h.workflow_id)
            flow.definition = graph
            await db.commit()
        plan_id = await due_plan(
            h, datetime.now(timezone.utc).replace(second=0, microsecond=0)
        )
        async with h.sessions() as db:
            occurrence = await timer_dispatch.materialize_due(db, plan_id)
            identifier = await timer_dispatch.dispatch_occurrence(
                db, occurrence
            )
            assert identifier is not None
        monkeypatch.setattr(tasks, "workflow_task_session", h.sessions)
        await tasks._run_workflow(
            identifier, h.workflow_id, {}, str(h.project_id)
        )
        async with h.sessions() as db:
            run = await db.get(WorkflowExecution, identifier)
            assert run.status == "waiting_approval"
            approval = await db.scalar(
                select(WorkflowApprovalRequest).where(
                    WorkflowApprovalRequest.execution_id == identifier,
                )
            )
            assert approval is not None
            approval_id = approval.id
            digest = approval_preview_digest(
                approval.id, identifier, approval.summary
            )
        yield h, identifier, approval_id, digest, plan_id


@pytest.mark.asyncio
async def test_timer_approval_continues_once_without_replaying_trigger(
    monkeypatch,
):
    async with waiting_timer(monkeypatch) as (
        h,
        identifier,
        approval,
        digest,
        _plan,
    ):
        async with h.sessions() as db:
            await decide_approval(
                db,
                str(h.project_id),
                approval,
                str(h.actor_id),
                "approve",
                expected_preview_digest=digest,
            )
        for _ in range(2):
            await tasks._run_workflow(
                identifier, h.workflow_id, {}, str(h.project_id)
            )
        async with h.sessions() as db:
            run = await db.get(WorkflowExecution, identifier)
            nodes = (
                await db.scalars(
                    select(NodeExecution).where(
                        NodeExecution.execution_id == identifier,
                    )
                )
            ).all()
            request = await db.get(WorkflowApprovalRequest, approval)
            assert run.status == "completed"
            assert "审批后完成：" in run.output["result"]
            assert [node.node_id for node in nodes] == [
                "clock",
                "approval",
                "answer",
            ]
            assert run.input is None
            assert request.checkpoint["capture_status"] == "redacted"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "disable",
        "rebind",
        "source_revoked",
        "approver_revoked",
        "engine_disabled",
    ],
)
async def test_timer_approval_cannot_continue_after_authority_changes(
    monkeypatch, change
):
    async with waiting_timer(monkeypatch) as (
        h,
        identifier,
        approval,
        digest,
        plan_id,
    ):
        async with h.sessions() as db:
            await decide_approval(
                db,
                str(h.project_id),
                approval,
                str(h.actor_id),
                "approve",
                expected_preview_digest=digest,
            )
            if change == "disable":
                await workflow_schedules.disable_schedule(
                    db,
                    h.workflow_id,
                    h.project_id,
                    h.actor_id,
                    ScheduleDisable(request_id=uuid4(), expected_revision=1),
                )
            elif change == "rebind":
                plan = await db.get(WorkflowSchedule, plan_id)
                plan.revision += 1
                await db.commit()
        if change == "source_revoked":
            monkeypatch.setattr(
                timer_runtime,
                "verify_timer_authority",
                AsyncMock(side_effect=PermissionError()),
            )
        elif change == "approver_revoked":
            monkeypatch.setattr(
                tasks,
                "verify_approval_assignees",
                AsyncMock(side_effect=PermissionError()),
            )
        elif change == "engine_disabled":
            monkeypatch.setattr(settings, "TIMER_EXECUTION_ENABLED", False)
        await tasks._run_workflow(
            identifier, h.workflow_id, {}, str(h.project_id)
        )
        async with h.sessions() as db:
            run = await db.get(WorkflowExecution, identifier)
            nodes = (
                await db.scalars(
                    select(NodeExecution).where(
                        NodeExecution.execution_id == identifier,
                    )
                )
            ).all()
            request = await db.get(WorkflowApprovalRequest, approval)
            assert run.status in {"cancelled", "failed"}
            assert run.output is None and run.input is None
            assert [node.node_id for node in nodes] == ["clock", "approval"]
            assert request.checkpoint["capture_status"] == "redacted"


def test_timer_approval_only_permits_continuation_without_customer_action(
    monkeypatch,
):
    from tests.unit.test_workflow_schedules import definition

    monkeypatch.setattr(settings, "TIMER_EXECUTION_ENABLED", True)
    graph = timer_graph(definition(), str(uuid4()))
    assert not ValidationService.validate_workflow(graph)
    graph["nodes"][1]["data"].update(
        {
            "after_approval_action": "create_aftersales_review",
            "case_category": "return_request",
        }
    )
    errors = ValidationService.validate_workflow(graph)
    assert any("定时" in error and "客户" in error for error in errors)
