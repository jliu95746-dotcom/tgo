import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.pool import NullPool

from celery_app import task_database


def test_each_task_gets_and_disposes_its_own_engine(monkeypatch):
    engines = []
    sessions = []

    def create_engine(url, **options):
        assert options["poolclass"] is NullPool
        engine = AsyncMock()
        engines.append(engine)
        return engine

    def create_session(engine, **options):
        session = AsyncMock()
        sessions.append(session)
        return session

    monkeypatch.setattr(task_database, "create_async_engine", create_engine)
    monkeypatch.setattr(task_database, "AsyncSession", create_session)

    async def run(fail=False):
        async with task_database.workflow_task_session():
            if fail:
                raise RuntimeError("controlled failure")

    asyncio.run(run())
    asyncio.run(run())
    with pytest.raises(RuntimeError, match="controlled failure"):
        asyncio.run(run(fail=True))
    assert len(engines) == 3
    for engine in engines:
        engine.dispose.assert_awaited_once()
    for session in sessions:
        session.__aexit__.assert_awaited_once()
