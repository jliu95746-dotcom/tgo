"""Due official G4 protocols freeze automatically without cross-project starvation."""

import asyncio
from datetime import datetime, timedelta, timezone
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.shadow_review_protocol import ShadowReviewProtocol
from app.schemas.shadow_sample import ShadowProtocolCreateRequest
from app.services.shadow_review_protocol_service import ShadowReviewProtocolService
from app.tasks.finalize_shadow_protocols import finalize_due_protocols
from tests.test_shadow_review_protocol_service import db as shared_protocol_db
from tests.test_shadow_sample_service import add_case

db = shared_protocol_db


def register_due(db, project_id, creator_id, start, end):
    return ShadowReviewProtocolService(db).register(
        project_id,
        creator_id,
        ShadowProtocolCreateRequest(
            request_id=uuid4(),
            window_start=start,
            window_end=end,
            sample_size=2,
        ),
    )


def test_due_protocol_freezes_once_and_keeps_future_protocol_scheduled(db, monkeypatch):
    from app.services import shadow_review_protocol_service as module

    now = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
    current = {"now": now}
    monkeypatch.setattr(module, "_utc_now", lambda: current["now"])
    ended_project, future_project = uuid4(), uuid4()
    visitor, staff = uuid4(), uuid4()
    start = now + timedelta(hours=1)
    end = start + timedelta(hours=1)
    due = register_due(db, ended_project, uuid4(), start, end)
    future = register_due(
        db,
        future_project,
        uuid4(),
        end + timedelta(hours=1),
        end + timedelta(hours=2),
    )
    add_case(
        db,
        project_id=ended_project,
        visitor_id=visitor,
        staff_id=staff,
        at=start + timedelta(minutes=10),
    )
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    before = finalize_due_protocols(factory, now=current["now"])
    assert before.frozen == before.empty == before.failed == 0

    current["now"] = end + timedelta(seconds=1)
    result = finalize_due_protocols(factory, now=current["now"])
    assert (result.frozen, result.empty, result.failed) == (1, 0, 0)
    assert ShadowReviewProtocolService(db).get(ended_project).id == due.id
    assert ShadowReviewProtocolService(db).get(ended_project).status == "frozen"
    assert ShadowReviewProtocolService(db).get(future_project).id == future.id
    assert ShadowReviewProtocolService(db).get(future_project).status == "scheduled"
    assert finalize_due_protocols(factory, now=current["now"]).frozen == 0
    assert (
        db.query(ShadowReviewProtocol).filter_by(project_id=ended_project).count() == 1
    )


def test_one_invalid_project_does_not_prevent_other_due_projects(db, monkeypatch):
    from app.services import shadow_review_protocol_service as module

    now = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
    current = {"now": now}
    monkeypatch.setattr(module, "_utc_now", lambda: current["now"])
    start, end = now + timedelta(hours=1), now + timedelta(hours=2)
    broken_project, valid_project = uuid4(), uuid4()
    register_due(db, broken_project, uuid4(), start, end)
    register_due(db, valid_project, uuid4(), start, end)
    broken = db.query(ShadowReviewProtocol).filter_by(project_id=broken_project).one()
    broken.algorithm = "unsupported-version"
    db.commit()
    current["now"] = end + timedelta(seconds=1)

    result = finalize_due_protocols(
        sessionmaker(bind=db.get_bind(), expire_on_commit=False),
        now=current["now"],
    )
    assert (result.frozen, result.empty, result.failed) == (0, 1, 1)
    assert ShadowReviewProtocolService(db).get(broken_project).status == "scheduled"
    assert ShadowReviewProtocolService(db).get(valid_project).status == "empty"


def test_sweep_keeps_the_original_cycle_when_another_worker_starts_next(
    db, monkeypatch
):
    from app.services import shadow_review_protocol_service as module

    now = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
    start, end = now + timedelta(hours=1), now + timedelta(hours=2)
    project = uuid4()
    monkeypatch.setattr(module, "_utc_now", lambda: now)
    previous = register_due(db, project, uuid4(), start, end)
    monkeypatch.setattr(module, "_utc_now", lambda: end + timedelta(seconds=1))
    base_factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    calls = 0

    def session_factory():
        nonlocal calls
        calls += 1
        if calls == 2:
            with base_factory() as other:
                ShadowReviewProtocolService(other).finalize(project)
                register_due(
                    other,
                    project,
                    uuid4(),
                    end + timedelta(hours=1),
                    end + timedelta(hours=2),
                )
        return base_factory()

    result = finalize_due_protocols(session_factory, now=end + timedelta(seconds=1))
    assert result.failed == 0
    assert result.skipped == 1
    assert result.frozen == result.empty == 0
    assert (
        db.query(ShadowReviewProtocol).filter_by(id=previous.id).one().batch_id is None
    )
    assert ShadowReviewProtocolService(db).get(project).status == "scheduled"


@pytest.mark.asyncio
async def test_periodic_finalization_starts_only_when_enabled(monkeypatch):
    from app.tasks import finalize_shadow_protocols as task_module

    completed = Event()

    def sweep():
        completed.set()
        return task_module.FinalizationSweep()

    monkeypatch.setattr(task_module, "finalize_due_protocols", sweep)
    monkeypatch.setattr(
        task_module.settings, "SHADOW_PROTOCOL_AUTO_FINALIZE_ENABLED", False
    )
    task_module.start_shadow_protocol_finalization()
    assert task_module._task is None

    monkeypatch.setattr(
        task_module.settings, "SHADOW_PROTOCOL_AUTO_FINALIZE_ENABLED", True
    )
    monkeypatch.setattr(
        task_module.settings, "SHADOW_PROTOCOL_FINALIZE_INTERVAL_SECONDS", 3600
    )
    task_module.start_shadow_protocol_finalization()
    try:
        assert await asyncio.wait_for(asyncio.to_thread(completed.wait, 2), 3)
    finally:
        await task_module.stop_shadow_protocol_finalization()
    assert task_module._task is None
