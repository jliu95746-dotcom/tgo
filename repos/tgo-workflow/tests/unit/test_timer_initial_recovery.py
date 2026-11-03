"""Both the recovery scan and its SQL reservation must admit timer sources."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models.execution import WorkflowExecution
from celery_app import tasks
from tests.unit.test_workflow_schedules import schedule_database


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source, expected",
    [
        ("scheduled_timer", 1),
        ("external_webhook", 1),
        ("internal_event", 1),
        ("unknown_source", 0),
    ],
)
async def test_initial_recovery_reservation_matches_scanner(
    monkeypatch,
    source,
    expected,
):
    async with schedule_database() as fixture:
        now = datetime.now(timezone.utc)
        identifier = str(uuid4())
        async with fixture.sessions() as db:
            db.add(
                WorkflowExecution(
                    id=identifier,
                    project_id=str(fixture.project_id),
                    workflow_id=fixture.workflow_id,
                    status="pending",
                    source_channel=source,
                    execution_mode="production",
                    release_id=fixture.release_id,
                    definition_digest=fixture.digest,
                    definition_snapshot={"nodes": [], "edges": []},
                    workflow_version=1,
                    input={},
                    started_at=now - timedelta(minutes=3),
                )
            )
            await db.commit()
        enqueue = AsyncMock()
        monkeypatch.setattr(tasks, "workflow_task_session", fixture.sessions)
        monkeypatch.setattr(tasks, "enqueue_pending_initial", enqueue)
        assert await tasks.sweep_pending_initial(now=now) == expected
        assert enqueue.await_count == expected
        if expected:
            assert enqueue.await_args.args[0].id == identifier
        assert await tasks.sweep_pending_initial(now=now) == 0
