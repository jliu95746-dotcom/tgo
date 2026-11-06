"""Independent PostgreSQL workers recover accepted manual handoffs."""

import asyncio
import importlib
from datetime import datetime, timedelta
from threading import Event
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import ManualServiceRequest, Visitor, VisitorWaitingQueue
from app.services import human_handoff_service, transfer_service
from tests import test_staff_assignment_revalidation_postgres as guard_checks

postgres_engine = guard_checks.postgres_engine
identity = guard_checks.identity
live_dispatch = guard_checks.live_dispatch
pytestmark = guard_checks.pytestmark


@pytest.fixture
def handoff(live_dispatch, monkeypatch):
    state, _run = live_dispatch
    state.visitor.service_status = "new"
    state.visitor.ai_disabled = False
    state.visitor.service_mode = "auto"
    state.session.status = "closed"
    state.entry.status = "cancelled"
    state.db.commit()
    monkeypatch.setattr(settings, "CUSTOMER_TURNS_ENABLED", False)
    monkeypatch.setattr(
        human_handoff_service, "notify_visitor_profile_updated", AsyncMock()
    )
    monkeypatch.setattr(human_handoff_service, "_notify_handoff", AsyncMock())
    try:
        yield state
    finally:
        state.db.rollback()
        state.db.query(ManualServiceRequest).filter_by(
            project_id=state.project.id,
        ).delete(synchronize_session=False)
        state.db.commit()


def recovery():
    return importlib.import_module("app.services.handoff_dispatch_recovery")


def current_request(state):
    return state.db.query(ManualServiceRequest).filter_by(
        project_id=state.project.id, visitor_id=state.visitor.id,
    ).one()


async def interrupt(state, monkeypatch, *, after_assignment=False):
    async def crash(**kwargs):
        if after_assignment:
            result = await transfer_service.transfer_to_staff(**kwargs)
            assert result.success and result.assigned_staff_id is not None
        raise asyncio.CancelledError

    with monkeypatch.context() as scope:
        scope.setattr(human_handoff_service, "transfer_to_staff", crash)
        with pytest.raises(asyncio.CancelledError):
            await human_handoff_service.request_human_handoff(
                db=state.db, project=state.project, visitor=state.visitor,
                reason="SCENARIO:guard_unchanged",
                source_message_id="synthetic-handoff-recovery",
                strict_source_receipt=True,
            )
    request = current_request(state)
    request.updated_at = datetime.utcnow() - timedelta(minutes=10)
    state.db.commit()
    return request


async def test_pending_request_has_a_durable_recovery_binding(
    handoff, monkeypatch,
):
    request = await interrupt(handoff, monkeypatch)
    with Session(handoff.db.get_bind()) as other:
        persisted = other.get(ManualServiceRequest, request.id)
        assert isinstance(persisted.request_metadata.get(
            "handoff_dispatch_pending",
        ), dict)
        visitor = other.get(Visitor, handoff.visitor.id)
        assert visitor.ai_disabled and visitor.service_mode == "manual"


async def test_recovered_queue_runs_existing_real_http_dispatch(
    handoff, monkeypatch,
):
    state = handoff
    request = await interrupt(state, monkeypatch)
    result = await recovery().recover_pending_handoffs(state.db)
    assert result.recovered == 1
    with Session(state.db.get_bind()) as other:
        entry = other.query(VisitorWaitingQueue).filter_by(
            project_id=state.project.id, visitor_id=state.visitor.id,
            status="waiting",
        ).one()
        received = other.get(ManualServiceRequest, request.id)
        assert received.request_metadata["handoff_receipt"]["entry_id"] == (
            str(entry.id)
        )
        assert received.request_metadata["notification_status"] == "unknown"
        assigned = await transfer_service.transfer_to_staff(
            other, state.visitor.id, state.project.id,
            session_id=entry.session_id, expected_queue_entry_id=entry.id,
            skip_queue_status_check=True, add_to_queue_if_no_staff=False,
            visitor_message="SCENARIO:guard_unchanged", ai_disabled=True,
            send_notification=False,
        )
        assert assigned.success and assigned.assigned_staff_id is not None
        assert entry.status == "assigned"
    state.complete.assert_awaited_once()
    assert (await recovery().recover_pending_handoffs(state.db)).checked == 0


async def test_assignment_commit_is_adopted_without_another_http_call(
    handoff, monkeypatch,
):
    state = handoff
    request = await interrupt(state, monkeypatch, after_assignment=True)
    assert state.complete.await_count == 1
    result = await recovery().recover_pending_handoffs(state.db)
    assert result.recovered == 1
    assert request.status == "in_progress"
    assert request.request_metadata["handoff_receipt"]["status"] == "active"
    assert state.complete.await_count == 1


@pytest.mark.parametrize("change", ["resume", "assist", "retire"])
async def test_committed_operator_change_is_not_reverted(
    handoff, monkeypatch, change,
):
    state = handoff
    request = await interrupt(state, monkeypatch)
    with Session(state.db.get_bind()) as other:
        visitor = other.get(Visitor, state.visitor.id)
        if change == "resume":
            visitor.ai_disabled = False
        elif change == "assist":
            visitor.service_mode = "assist"
        else:
            visitor.access_retired_at = datetime.utcnow()
        other.commit()
    result = await recovery().recover_pending_handoffs(state.db)
    assert result.cancelled == 1 and result.recovered == 0
    state.db.expire_all()
    assert request.request_metadata["handoff_receipt"]["status"] == "cancelled"
    assert state.db.query(VisitorWaitingQueue).filter_by(
        project_id=state.project.id, status="waiting",
    ).count() == 0
    state.complete.assert_not_awaited()


async def test_rollback_does_not_publish_a_recovered_queue(
    handoff, monkeypatch,
):
    state = handoff
    request = await interrupt(state, monkeypatch)
    module = recovery()
    with monkeypatch.context() as scope:
        def fail(*_args, **_kwargs):
            raise RuntimeError("synthetic commit interruption")

        scope.setattr(module, "_finish", fail)
        result = await module.recover_pending_handoffs(state.db)
    assert result.failed == 1 and result.recovered == 0
    with Session(state.db.get_bind()) as other:
        assert other.query(VisitorWaitingQueue).filter_by(
            project_id=state.project.id, status="waiting",
        ).count() == 0
        assert "handoff_dispatch_pending" in other.get(
            ManualServiceRequest, request.id,
        ).request_metadata
        assert other.get(Visitor, state.visitor.id).service_status == "new"
    result = await module.recover_pending_handoffs(state.db)
    assert result.recovered == 1


async def test_two_independent_workers_cannot_create_two_queue_entries(
    handoff, monkeypatch,
):
    state = handoff
    await interrupt(state, monkeypatch)
    module = recovery()
    entered, release = Event(), Event()
    original = module._add_to_waiting_queue

    async def pause(*args, **kwargs):
        entered.set()
        assert release.wait(15), "Test worker was not released"
        return await original(*args, **kwargs)

    monkeypatch.setattr(module, "_add_to_waiting_queue", pause)
    engine = state.db.get_bind()

    def work():
        with Session(engine) as db:
            return asyncio.run(module.recover_pending_handoffs(db))

    first = asyncio.create_task(asyncio.to_thread(work))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        with Session(engine) as other:
            second = await module.recover_pending_handoffs(other)
        assert second.recovered == 0 and second.failed == 0
    finally:
        release.set()
        result = await first
    assert result.recovered == 1
    assert state.db.query(VisitorWaitingQueue).filter_by(
        project_id=state.project.id, status="waiting",
    ).count() == 1
