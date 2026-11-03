"""Waiting source events are inspected fairly without opening business input."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.config import settings
from app.models.approval import WorkflowApprovalRequest
from app.models.execution import WorkflowExecution
from app.services import event_source_audit as audit
from app.services.event_trigger import accept_event
from app.services.approval_state import decide_approval
from app.services.approval_preview import approval_preview_digest
from celery_app.celery import celery_app
from tests.unit.test_event_source_binding import (
    binding,
    request_for,
    source_store as _shared_source_store,
)

source_store = _shared_source_store


class Progress:
    def __init__(self, cursor=None):
        self.last = cursor
        self.owner = None
        self.advanced = []

    async def acquire(self, owner):
        if self.owner is not None:
            return False
        self.owner = owner
        return True

    async def cursor(self):
        return self.last

    async def advance(self, owner, cursor):
        if owner != self.owner:
            return False
        self.last = cursor or None
        self.advanced.append(cursor)
        return True

    async def release(self, owner):
        if owner == self.owner:
            self.owner = None


async def waiting(store, status="waiting_approval"):
    sessions, project, workflow, context, _ = store
    source = binding()
    async with sessions() as db:
        accepted = await accept_event(
            db,
            workflow,
            request_for(project, context, source),
            "source-" + str(source.delivery_id),
            "synthetic-http-key",
        )
        run = await db.get(WorkflowExecution, accepted.execution_id)
        run.status = status
        # A synthetic wait exercises authority lifecycle; this does not open
        # unsupported Event/approval publication or execute a customer action.
        request = WorkflowApprovalRequest(
            project_id=project,
            execution_id=run.id,
            node_id="approval",
            approver_staff_id=str(uuid4()),
            summary="合成来源巡检待确认",
            checkpoint={
                "capture_status": "encrypted",
                "key_id": "missing",
                "ciphertext": "synthetic-unreadable-cursor",
            },
            status="approved" if status == "pending_resume" else "pending",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        db.add(request)
        await db.flush()
        run.active_approval_id = request.id
        await db.commit()
        return run.id, request.id, str(source.delivery_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["waiting_approval", "pending_resume"])
async def test_revoked_wait_cleans_without_payload_or_checkpoint_keys(
    source_store,
    monkeypatch,
    status,
):
    sessions, _, _, _, verify = source_store
    identifier, request_id, _ = await waiting(source_store, status)
    monkeypatch.setattr(settings, "WORKFLOW_EVENT_INPUT_KEYS", None)
    monkeypatch.setattr(settings, "WORKFLOW_APPROVAL_KEYS", None)
    verify.side_effect = PermissionError("source-revoked")
    progress = Progress()
    assert await audit.audit_waiting_event_sources(sessions, progress) == 1
    assert progress.last == identifier and progress.owner is None
    async with sessions() as db:
        run = await db.get(WorkflowExecution, identifier)
        request = await db.get(WorkflowApprovalRequest, request_id)
        assert run.status == "cancelled" and run.input is None
        assert request.checkpoint["capture_status"] == "redacted"
        assert request.status == (
            "approved" if status == "pending_resume" else "cancelled"
        )
    assert await audit.audit_waiting_event_sources(sessions, progress) == 0


@pytest.mark.asyncio
async def test_unknown_authority_preserves_wait_and_retries_on_next_cycle(source_store):
    sessions, _, _, _, verify = source_store
    identifier, request_id, _ = await waiting(source_store)
    async with sessions() as db:
        encrypted = dict((await db.get(WorkflowExecution, identifier)).input)
        cursor = dict((await db.get(WorkflowApprovalRequest, request_id)).checkpoint)
    progress = Progress()
    verify.side_effect = RuntimeError("temporary-private-service-failure")
    assert await audit.audit_waiting_event_sources(sessions, progress) == 0
    async with sessions() as db:
        assert (await db.get(WorkflowExecution, identifier)).input == encrypted
        assert (
            await db.get(WorkflowExecution, identifier)
        ).status == "waiting_approval"
        assert (await db.get(WorkflowApprovalRequest, request_id)).checkpoint == cursor
    verify.side_effect = None
    assert await audit.audit_waiting_event_sources(sessions, progress) == 0
    verify.side_effect = PermissionError("now-revoked")
    assert await audit.audit_waiting_event_sources(sessions, progress) == 1


@pytest.mark.asyncio
async def test_durable_cursor_reaches_later_waits_after_worker_restart(source_store):
    sessions, _, _, _, verify = source_store
    seeded = [await waiting(source_store) for _ in range(8)]
    ordered = sorted(seeded)
    revoked_delivery = ordered[-1][2]
    observed = []

    async def authority(query):
        delivery = str(query.source_binding.delivery_id)
        observed.append(delivery)
        if delivery == revoked_delivery:
            raise PermissionError("source-revoked")

    verify.side_effect = authority
    progress = Progress()
    assert await audit.audit_waiting_event_sources(sessions, progress, limit=3) == 0
    restarted = Progress(progress.last)
    assert await audit.audit_waiting_event_sources(sessions, restarted, limit=3) == 0
    assert await audit.audit_waiting_event_sources(sessions, restarted, limit=3) == 1
    assert observed == [row[2] for row in ordered]
    assert await audit.audit_waiting_event_sources(sessions, restarted, limit=3) == 0
    assert observed[8:] == [row[2] for row in ordered[:3]]


@pytest.mark.asyncio
async def test_busy_audit_does_not_touch_database_or_another_lease():
    sessions = AsyncMock()
    progress = Progress()
    progress.owner = "other-owner"
    assert await audit.audit_waiting_event_sources(sessions, progress) == 0
    sessions.assert_not_called()
    assert progress.owner == "other-owner" and progress.advanced == []


@pytest.mark.asyncio
async def test_time_budget_keeps_last_finished_cursor(source_store):
    sessions, _, _, _, verify = source_store
    seeded = sorted([await waiting(source_store) for _ in range(2)])
    verify.reset_mock()
    ticks = iter([0, 0, 46])
    progress = Progress()
    assert (
        await audit.audit_waiting_event_sources(
            sessions,
            progress,
            clock=lambda: next(ticks),
        )
        == 0
    )
    assert verify.await_count == 1
    assert progress.last == seeded[0][0] and progress.owner is None


@pytest.mark.asyncio
async def test_concurrent_approval_is_not_scrubbed_by_stale_wait_check(source_store):
    sessions, project, _, _, verify = source_store
    identifier, request_id, _ = await waiting(source_store)
    changed = False

    async def approve_then_revoke(_query):
        nonlocal changed
        if not changed:
            async with sessions() as db:
                request = await db.get(WorkflowApprovalRequest, request_id)
                await decide_approval(
                    db,
                    project,
                    request_id,
                    request.approver_staff_id,
                    "approve",
                    expected_preview_digest=approval_preview_digest(
                        request_id,
                        identifier,
                        request.summary,
                    ),
                )
            changed = True
        raise PermissionError("source-revoked")

    verify.side_effect = approve_then_revoke
    progress = Progress()
    assert await audit.audit_waiting_event_sources(sessions, progress) == 0
    async with sessions() as db:
        assert (await db.get(WorkflowExecution, identifier)).status == "pending_resume"
        assert (await db.get(WorkflowApprovalRequest, request_id)).checkpoint[
            "capture_status"
        ] == "encrypted"
    assert await audit.audit_waiting_event_sources(sessions, progress) == 1
    async with sessions() as db:
        request = await db.get(WorkflowApprovalRequest, request_id)
        assert request.status == "approved"
        assert request.checkpoint["capture_status"] == "redacted"


def test_audit_task_uses_the_deployment_queue_and_is_registered():
    item = celery_app.conf.beat_schedule["audit-waiting-event-sources"]
    assert item["task"] == "audit_waiting_event_sources"
    assert item["schedule"] == 60.0
    assert item["options"]["queue"] == settings.WORKFLOW_QUEUE_NAME
    assert "celery_app.event_source_tasks" in celery_app.conf.include


@pytest.mark.asyncio
async def test_audit_excludes_non_source_draft_and_terminal_runs(source_store):
    sessions, _, _, _, verify = source_store
    seeded = [await waiting(source_store) for _ in range(5)]
    async with sessions() as db:
        for (identifier, _, _), changes in zip(
            seeded[1:],
            (
                {"source_channel": "webhook"},
                {"execution_mode": "debug"},
                {"status": "cancelled"},
                {"visitor_session_id": None},
            ),
        ):
            run = await db.get(WorkflowExecution, identifier)
            for field, value in changes.items():
                setattr(run, field, value)
        await db.commit()
    verify.reset_mock()
    verify.side_effect = PermissionError("source-revoked")
    assert await audit.audit_waiting_event_sources(sessions, Progress()) == 1
    assert verify.await_count == 1
    async with sessions() as db:
        assert (
            await db.get(WorkflowExecution, seeded[1][0])
        ).status == "waiting_approval"
        assert (
            await db.get(WorkflowExecution, seeded[2][0])
        ).status == "waiting_approval"
        assert (
            await db.get(WorkflowExecution, seeded[4][0])
        ).status == "waiting_approval"


@pytest.mark.asyncio
async def test_authority_timeout_preserves_wait_and_releases_lease(
    source_store,
):
    import asyncio

    sessions, _, _, _, verify = source_store
    identifier, request_id, _ = await waiting(source_store)

    # Inject the deadline at the network boundary, after the read transaction
    # is released. Cancelling an in-memory SQLite query destroys its database.
    verify.side_effect = asyncio.TimeoutError()
    progress = Progress()
    assert await audit.audit_waiting_event_sources(sessions, progress) == 0
    assert progress.owner is None and progress.last == identifier
    async with sessions() as db:
        assert (
            await db.get(WorkflowExecution, identifier)
        ).status == "waiting_approval"
        assert (await db.get(WorkflowApprovalRequest, request_id)).status == "pending"


def test_task_retry_does_not_publish_database_error_details(monkeypatch):
    from unittest.mock import Mock
    from celery.exceptions import Retry
    from celery_app import event_source_tasks as tasks

    monkeypatch.setattr(
        tasks,
        "sweep_waiting_event_sources",
        AsyncMock(side_effect=RuntimeError("synthetic-sensitive-database-detail")),
    )
    retry = Mock(side_effect=Retry())
    monkeypatch.setattr(tasks.audit_waiting_event_sources_task, "retry", retry)
    with pytest.raises(Retry):
        tasks.audit_waiting_event_sources_task.run()
    assert "synthetic-sensitive" not in str(retry.call_args.kwargs["exc"])


def test_repeated_task_runs_close_their_own_http_pool(monkeypatch):
    import asyncio
    from app.integrations.http_client import HttpClient
    from celery_app import event_source_tasks as tasks

    seen = []
    monkeypatch.setattr(HttpClient, "_client", None)

    async def inspect_task_loop():
        client = await HttpClient.get_client()
        seen.append((client, asyncio.get_running_loop()))
        return 0

    monkeypatch.setattr(tasks, "sweep_waiting_event_sources", inspect_task_loop)
    try:
        assert tasks.audit_waiting_event_sources_task.run() == 0
        assert seen[0][0].is_closed
        assert tasks.audit_waiting_event_sources_task.run() == 0
        assert seen[1][0].is_closed
        assert seen[0][0] is not seen[1][0] and seen[0][1] is not seen[1][1]
        assert HttpClient._client is None
    finally:
        asyncio.run(HttpClient.close_client())
