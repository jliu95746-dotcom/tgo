"""Queue refreshes follow the owning transaction, including savepoints."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.services import queue_events


@pytest.fixture
def event_db(monkeypatch):
    publish = AsyncMock()
    monkeypatch.setattr(queue_events, "publish_queue_updates", publish)
    engine = create_engine("sqlite://")
    with Session(engine) as db:
        yield db, publish
    engine.dispose()


@pytest.mark.asyncio
async def test_only_outer_commit_publishes_once_and_entered_wins(event_db):
    db, publish = event_db
    project_id = uuid4()
    db.begin()
    queue_events.schedule_queue_update(db, project_id)
    queue_events.schedule_queue_update(db, project_id, "entered")
    queue_events.schedule_queue_update(db, project_id)
    publish.assert_not_called()
    db.commit()
    await asyncio.sleep(0)
    publish.assert_awaited_once_with({project_id: "entered"})


@pytest.mark.asyncio
async def test_rollback_does_not_publish_or_leak_into_next_transaction(event_db):
    db, publish = event_db
    db.begin()
    queue_events.schedule_queue_update(db, uuid4(), "entered")
    db.rollback()
    with db.begin():
        pass
    await asyncio.sleep(0)
    publish.assert_not_called()


@pytest.mark.asyncio
async def test_nested_commit_waits_for_outer_commit(event_db):
    db, publish = event_db
    first, second = uuid4(), uuid4()
    with db.begin():
        queue_events.schedule_queue_update(db, first)
        with db.begin_nested():
            queue_events.schedule_queue_update(db, first, "entered")
            queue_events.schedule_queue_update(db, second)
        await asyncio.sleep(0)
        publish.assert_not_called()
    await asyncio.sleep(0)
    publish.assert_awaited_once_with({first: "entered", second: "updated"})


@pytest.mark.asyncio
async def test_nested_rollback_keeps_only_outer_changes(event_db):
    db, publish = event_db
    first, second = uuid4(), uuid4()
    with db.begin():
        queue_events.schedule_queue_update(db, first)
        nested = db.begin_nested()
        queue_events.schedule_queue_update(db, first, "entered")
        queue_events.schedule_queue_update(db, second)
        nested.rollback()
    await asyncio.sleep(0)
    publish.assert_awaited_once_with({first: "updated"})


@pytest.mark.asyncio
async def test_nested_commit_is_discarded_on_outer_rollback(event_db):
    db, publish = event_db
    db.begin()
    with db.begin_nested():
        queue_events.schedule_queue_update(db, uuid4(), "entered")
    db.rollback()
    await asyncio.sleep(0)
    publish.assert_not_called()


@pytest.mark.asyncio
async def test_close_discards_pending_changes_when_session_is_reused(event_db):
    db, publish = event_db
    db.begin()
    queue_events.schedule_queue_update(db, uuid4(), "entered")
    db.close()
    with db.begin():
        pass
    await asyncio.sleep(0)
    publish.assert_not_called()
    assert not db.info.get(queue_events._KEY)
