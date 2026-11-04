"""Run the worker's read-only schema check against real SQLite tables."""

from contextlib import asynccontextmanager

import pytest
from app.models.workflow_timer_occurrence import WorkflowTimerOccurrence
from app.services import timer_runtime_health as health
from tests.unit.test_workflow_schedules import schedule_database


@pytest.mark.asyncio
async def test_schema_probe_accepts_migrated_tables_and_rejects_missing_table(monkeypatch):
    async with schedule_database() as fixture:
        @asynccontextmanager
        async def task_session():
            async with fixture.sessions() as session:
                yield session

        monkeypatch.setattr(health, "workflow_task_session", task_session)
        assert await health.timer_database_ready()
        async with fixture.sessions() as session:
            connection = await session.connection()
            await connection.run_sync(WorkflowTimerOccurrence.__table__.drop)
            await session.commit()
        assert not await health.timer_database_ready()
