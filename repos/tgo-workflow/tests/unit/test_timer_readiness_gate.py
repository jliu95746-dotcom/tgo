"""Only fresh runtime evidence enables new schedules; replay remains immutable."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock
import pytest
from fastapi import HTTPException
from app.schemas.timer_runtime_health import TimerRuntimeState
from app.services import workflow_schedules as schedules
from tests.unit.test_workflow_schedules import request, schedule_database


def runtime(ready):
    return TimerRuntimeState(
        status="ready" if ready else "worker_waiting", ready=ready,
        checked_at=datetime.now(timezone.utc), scheduler_recent=True,
        worker_recent=ready, database_ready=ready,
    )


@pytest.mark.asyncio
async def test_configured_engine_cannot_enable_without_runtime_evidence(monkeypatch):
    monkeypatch.setattr(schedules, "execution_available", lambda: True)
    monkeypatch.setattr(schedules, "get_timer_runtime_state", AsyncMock(return_value=runtime(False)))
    async with schedule_database() as fixture:
        async with fixture.sessions() as db:
            with pytest.raises(HTTPException) as error:
                await schedules.save_schedule(db, fixture.workflow_id, fixture.project_id,
                    fixture.actor_id, request(fixture, enabled=True))
            assert error.value.status_code == 503
        async with fixture.sessions() as db:
            state = await schedules.get_schedule(db, fixture.workflow_id, fixture.project_id)
            assert state.schedule is None and not state.execution_available
            assert state.runtime.status == "worker_waiting"


@pytest.mark.asyncio
async def test_completed_command_replays_even_if_runtime_is_no_longer_ready(monkeypatch):
    monkeypatch.setattr(schedules, "execution_available", lambda: True)
    health = AsyncMock(return_value=runtime(True))
    monkeypatch.setattr(schedules, "get_timer_runtime_state", health)
    async with schedule_database() as fixture:
        command = request(fixture, enabled=True)
        async with fixture.sessions() as db:
            original = await schedules.save_schedule(db, fixture.workflow_id,
                fixture.project_id, fixture.actor_id, command)
        health.side_effect = AssertionError("Replay must not depend on new liveness")
        async with fixture.sessions() as db:
            replay = await schedules.save_schedule(db, fixture.workflow_id,
                fixture.project_id, fixture.actor_id, command)
        assert replay.replayed and replay.schedule == original.schedule
        assert health.await_count == 1
