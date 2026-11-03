"""Stopping a source must fence and release paused approval continuations."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.database import Base
from app.models.approval import WorkflowApprovalRequest
from app.models.execution import WorkflowExecution
from app.models.workflow import Workflow
from app.models.workflow_release import WorkflowRelease
from app.models.workflow_schedule import WorkflowSchedule
from app.models.workflow_timer_occurrence import WorkflowTimerOccurrence
from app.models.workflow_webhook import WorkflowWebhook
from app.models.workflow_webhook_delivery import WorkflowWebhookDelivery
from app.services import approval_state, timer_runtime, timer_state
from app.services import webhook_lifecycle, webhook_runtime
from app.services.approval_preview import approval_preview_digest
from app.services.execution_lifecycle import ExecutionStopped


@asynccontextmanager
async def source_fixture(kind, status="waiting_approval"):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with seeded_source_fixture(engine, kind, status) as result:
            yield result
    finally:
        await engine.dispose()


@asynccontextmanager
async def seeded_source_fixture(engine, kind, status):
    tables = [
        Workflow,
        WorkflowRelease,
        WorkflowExecution,
        WorkflowApprovalRequest,
        WorkflowSchedule,
        WorkflowTimerOccurrence,
        WorkflowWebhook,
        WorkflowWebhookDelivery,
    ]
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda db: Base.metadata.create_all(
                db,
                tables=[model.__table__ for model in tables],
            )
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project, flow, actor, run_id, source_id, binding_id, approval_id = (
        str(uuid4()) for _ in range(7)
    )
    now = datetime.now(timezone.utc)
    async with sessions() as db:
        db.add(
            Workflow(
                id=flow,
                project_id=project,
                name="合成来源审批",
                definition={"nodes": [], "edges": []},
            )
        )
        await db.flush()
        run = WorkflowExecution(
            id=run_id,
            workflow_id=flow,
            project_id=project,
            status=status,
            execution_mode="production",
            workflow_version=1,
            source_channel=kind,
            source_message_id="synthetic-source",
            active_approval_id=approval_id,
            input={"synthetic": "private-continuation-text"},
            output=None,
        )
        db.add(run)
        await db.flush()
        request = WorkflowApprovalRequest(
            id=approval_id,
            execution_id=run_id,
            project_id=project,
            node_id="approval",
            approver_staff_id=actor,
            summary="合成核对",
            checkpoint={
                "context_data": {"private": "private-continuation-text"}
            },
            status="approved" if status == "pending_resume" else "pending",
            expires_at=now + timedelta(minutes=5),
        )
        db.add(request)
        if kind == "scheduled_timer":
            binding = WorkflowSchedule(
                id=binding_id,
                workflow_id=flow,
                project_id=project,
                revision=2,
                enabled=False,
                release_id="release-a",
                release_digest="a" * 64,
                cron_expression="* * * * *",
                timezone="UTC",
                created_by=actor,
                updated_by=actor,
            )
            db.add(binding)
            await db.flush()
            source = WorkflowTimerOccurrence(
                id=source_id,
                schedule_id=binding_id,
                project_id=project,
                workflow_id=flow,
                revision=1,
                scheduled_at=now,
                actor_staff_id=actor,
                release_id="release-a",
                release_digest="a" * 64,
                status="queued",
                attempts=1,
                execution_id=run_id,
                claimed_at=now,
                next_attempt_at=now,
                created_at=now,
                updated_at=now,
            )
        else:
            binding = WorkflowWebhook(
                id=binding_id,
                workflow_id=flow,
                project_id=project,
                revision=2,
                credential_revision=1,
                enabled=False,
                key_id="synthetic",
                credential_ciphertext="sealed-fixture",
                release_id="release-a",
                release_digest="a" * 64,
                actor_staff_id=actor,
                created_by=actor,
                updated_by=actor,
            )
            db.add(binding)
            await db.flush()
            source = WorkflowWebhookDelivery(
                id=source_id,
                hook_id=binding_id,
                project_id=project,
                workflow_id=flow,
                actor_staff_id=actor,
                binding_revision=1,
                credential_revision=1,
                event_key="b" * 64,
                payload_key_id="synthetic",
                payload_receipt="c" * 64,
                payload_ciphertext="encrypted-private-notification",
                release_id="release-a",
                release_digest="a" * 64,
                status="queued",
                attempts=1,
                next_attempt_at=now,
                expires_at=now + timedelta(minutes=5),
                execution_id=run_id,
                claimed_at=now,
                created_at=now,
                updated_at=now,
            )
        db.add(source)
        await db.commit()
    yield SimpleNamespace(
        sessions=sessions,
        project=project,
        flow=flow,
        actor=actor,
        run_id=run_id,
        source_id=source_id,
        binding_id=binding_id,
        approval_id=approval_id,
        now=now,
    )


async def assert_stopped(h, expected_status="cancelled"):
    async with h.sessions() as db:
        run = await db.get(WorkflowExecution, h.run_id)
        approval = await db.get(WorkflowApprovalRequest, h.approval_id)
        assert run.status == expected_status
        assert run.input is None and run.output is None
        assert approval.checkpoint["capture_status"] == "redacted"
        assert "private-continuation-text" not in json.dumps(
            approval.checkpoint
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["scheduled_timer", "external_webhook"])
@pytest.mark.parametrize(
    "status", ["pending", "running", "waiting_approval", "pending_resume"]
)
async def test_source_revision_change_stops_and_scrubs_approval(kind, status):
    async with source_fixture(kind, status) as h:
        async with h.sessions() as db:
            if kind == "scheduled_timer":
                binding = await db.get(WorkflowSchedule, h.binding_id)
                await timer_state.cancel_superseded(db, binding, h.now)
                source = await db.get(WorkflowTimerOccurrence, h.source_id)
            else:
                binding = await db.get(WorkflowWebhook, h.binding_id)
                await webhook_lifecycle.cancel_superseded(db, binding, h.now)
                source = await db.get(WorkflowWebhookDelivery, h.source_id)
                assert source.payload_ciphertext is None
            await db.commit()
            assert source.status == "cancelled"
        await assert_stopped(h)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["scheduled_timer", "external_webhook"])
async def test_disabled_runtime_scrubs_a_resumed_approval(kind, monkeypatch):
    async with source_fixture(kind, "running") as h:
        async with h.sessions() as db:
            if kind == "scheduled_timer":
                monkeypatch.setattr(
                    timer_runtime, "timer_capability_enabled", lambda: False
                )
                guard = timer_runtime.require_active_timer
            else:
                monkeypatch.setattr(
                    settings, "WEBHOOK_EXECUTION_ENABLED", False
                )
                guard = webhook_runtime.require_active_webhook
            with pytest.raises(ExecutionStopped):
                await guard(db, h.project, h.flow, h.run_id)
        await assert_stopped(
            h, "failed" if kind == "scheduled_timer" else "cancelled"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action, expected",
    [
        ("reject", "cancelled"),
        ("expire", "expired"),
        ("revoked", "failed"),
    ],
)
async def test_approval_terminal_paths_release_webhook_payload(
    action, expected
):
    status = "pending_resume" if action == "revoked" else "waiting_approval"
    async with source_fixture("external_webhook", status) as h:
        async with h.sessions() as db:
            if action == "reject":
                digest = approval_preview_digest(
                    h.approval_id, h.run_id, "合成核对"
                )
                await approval_state.decide_approval(
                    db,
                    h.project,
                    h.approval_id,
                    h.actor,
                    "reject",
                    expected_preview_digest=digest,
                )
            elif action == "expire":
                assert await approval_state.expire_approval(
                    db,
                    h.project,
                    h.approval_id,
                    now=h.now + timedelta(minutes=6),
                )
            else:
                await approval_state.fail_revoked_approved_resume(
                    db,
                    h.project,
                    h.run_id,
                    h.approval_id,
                )
            source = await db.get(WorkflowWebhookDelivery, h.source_id)
            assert source.status == expected
            assert source.payload_ciphertext is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["scheduled_timer", "external_webhook"])
async def test_source_update_preserves_already_completed_history(kind):
    async with source_fixture(kind, "completed") as h:
        async with h.sessions() as db:
            run = await db.get(WorkflowExecution, h.run_id)
            run.output = {"result": "已完成的历史"}
            run.input = None
            await db.commit()
            if kind == "scheduled_timer":
                binding = await db.get(WorkflowSchedule, h.binding_id)
                await timer_state.cancel_superseded(db, binding, h.now)
            else:
                binding = await db.get(WorkflowWebhook, h.binding_id)
                await webhook_lifecycle.cancel_superseded(db, binding, h.now)
            await db.commit()
            await db.refresh(run)
            assert run.status == "completed"
            assert run.output == {"result": "已完成的历史"}
