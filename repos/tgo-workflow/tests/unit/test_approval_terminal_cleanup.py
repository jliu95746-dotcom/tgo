"""Terminal cleanup cancels pending decisions and keeps active cursors intact."""

from datetime import UTC, datetime, timedelta

import pytest

from app.models.execution import WorkflowExecution
from app.services.approval_retention import scrub_terminal_approval_checkpoints
from app.services.approval_state import decide_approval, pause_for_approval
from tests.unit.test_approval_state import prepared_run, preview_for


@pytest.mark.asyncio
async def test_source_stop_cancels_pending_approval_in_the_same_cleanup():
    engine, sessions, project, execution, actor, cursor = await prepared_run("合成来源确认")
    try:
        async with sessions() as db:
            request = await pause_for_approval(
                db,
                project,
                execution,
                "approval",
                actor,
                "合成来源确认",
                cursor,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            run = await db.get(WorkflowExecution, execution)
            run.status, run.completed_at = "cancelled", datetime.now(UTC)
            await db.commit()
            await scrub_terminal_approval_checkpoints(db, project, execution)
            await db.commit()
            await db.refresh(request)
            assert request.status == "cancelled"
            assert request.decided_by is None
            assert request.decided_at.replace(tzinfo=UTC) == run.completed_at
            assert request.checkpoint["capture_status"] == "redacted"
            decided = request.decided_at
            await scrub_terminal_approval_checkpoints(db, project, execution)
            await db.commit()
            await db.refresh(request)
            assert request.decided_at == decided
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stale_cleanup_cannot_destroy_a_still_waiting_cursor():
    engine, sessions, project, execution, actor, cursor = await prepared_run("合成来源确认")
    try:
        async with sessions() as db:
            request = await pause_for_approval(
                db,
                project,
                execution,
                "approval",
                actor,
                "合成来源确认",
                cursor,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            encrypted = dict(request.checkpoint)
            await scrub_terminal_approval_checkpoints(db, project, execution)
            await db.commit()
            await db.refresh(request)
            assert request.status == "pending"
            assert request.checkpoint == encrypted
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_source_stop_preserves_a_committed_approval_decision():
    engine, sessions, project, execution, actor, cursor = await prepared_run("合成来源确认")
    try:
        async with sessions() as db:
            request = await pause_for_approval(
                db,
                project,
                execution,
                "approval",
                actor,
                "合成来源确认",
                cursor,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            await decide_approval(
                db,
                project,
                request.id,
                actor,
                "approve",
                expected_preview_digest=preview_for(request),
            )
            await db.refresh(request)
            decision_time = request.decided_at
            run = await db.get(WorkflowExecution, execution)
            run.status, run.completed_at = "cancelled", datetime.now(UTC)
            await db.commit()
            await scrub_terminal_approval_checkpoints(db, project, execution)
            await db.commit()
            await db.refresh(request)
            assert request.status == "approved" and request.decided_by == actor
            assert request.decided_at == decision_time
            assert request.checkpoint["capture_status"] == "redacted"
    finally:
        await engine.dispose()
