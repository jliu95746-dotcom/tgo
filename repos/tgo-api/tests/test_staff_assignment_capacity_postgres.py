"""Separate transactions cannot claim the same last staff seat."""

import asyncio
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timedelta
from threading import Barrier, Event

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import (
    Staff,
    Visitor,
    VisitorAssignmentHistory,
    VisitorSession,
    VisitorWaitingQueue,
)
from app.services import transfer_service
from tests import test_staff_assignment_revalidation_postgres as guard_checks

postgres_engine = guard_checks.postgres_engine
identity = guard_checks.identity
live_dispatch = guard_checks.live_dispatch
pytestmark = guard_checks.pytestmark
PRIVATE = guard_checks.PRIVATE


@pytest.fixture
def live_reception(live_dispatch):
    state, _run = live_dispatch
    state.rule.max_concurrent_chats = 4
    state.db.commit()

    async def run(case):
        return await transfer_service.transfer_to_staff(
            state.db,
            state.visitor.id,
            state.project.id,
            session_id=state.session.id,
            expected_queue_entry_id=state.entry.id,
            skip_queue_status_check=True,
            add_to_queue_if_no_staff=False,
            send_notification=False,
            ai_disabled=True,
            visitor_message="SCENARIO:seat_" + case,
        )

    return state, run


@pytest.mark.parametrize(
    "case",
    [
        "unchanged",
        "paused",
        "disabled",
        "inactive",
        "deleted",
        "agent",
        "capacity",
        "all_paused",
        "offline",
    ],
)
async def test_committed_staff_change_before_model_return(
    live_reception, caplog, case
):
    state, run = live_reception
    with caplog.at_level("INFO"):
        result = await run(case)
    assert result.success, result.message
    state.complete.assert_awaited_once()
    state.db.expire_all()
    history = (
        state.db.query(VisitorAssignmentHistory)
        .filter_by(
            project_id=state.project.id,
            visitor_id=state.visitor.id,
        )
        .one()
    )
    assert history.model_used == "fixture-model"
    if case in {"unchanged", "offline"}:
        assert history.assigned_staff_id == state.staff.id
        assert history.reasoning == PRIVATE
    else:
        assert history.llm_response is None
        assert PRIVATE not in history.reasoning
        if case == "all_paused":
            assert not history.candidate_staff_ids
            assert history.assigned_staff_id is None
            assert state.entry.status == "waiting"
            assert state.visitor.service_status == "queued"
            assert state.session.staff_id is None
        else:
            assert str(state.staff.id) not in history.candidate_staff_ids
            assert history.assigned_staff_id == state.colleague.id
    assert PRIVATE not in repr([vars(record) for record in caplog.records])


def one_seat(state):
    state.colleague.service_paused = True
    state.rule.llm_assignment_enabled = False
    state.rule.max_concurrent_chats = 1
    state.db.execute(
        update(VisitorSession)
        .where(
            VisitorSession.project_id == state.project.id,
            VisitorSession.staff_id == state.staff.id,
        )
        .values(status="closed")
    )
    state.db.commit()
    visitor = Visitor(
        project_id=state.project.id,
        platform_id=state.visitor.platform_id,
        platform_open_id=state.colleague.username,
        service_status="queued",
        ai_disabled=True,
    )
    state.db.add(visitor)
    state.db.flush()
    session = VisitorSession(
        project_id=state.project.id,
        platform_id=visitor.platform_id,
        visitor_id=visitor.id,
        status="open",
    )
    state.db.add(session)
    state.db.flush()
    entry = VisitorWaitingQueue(
        project_id=state.project.id,
        visitor_id=visitor.id,
        session_id=session.id,
        status="waiting",
        entered_at=datetime.utcnow(),
        expired_at=datetime.utcnow() + timedelta(minutes=5),
        ai_disabled=True,
    )
    state.db.add(entry)
    state.db.commit()
    return visitor.id, session.id, entry.id


async def transfer(
    db, project_id, identities, *, auto_commit=True, target_staff_id=None
):
    visitor_id, session_id, entry_id = identities
    return await transfer_service.transfer_to_staff(
        db,
        visitor_id,
        project_id,
        session_id=session_id,
        expected_queue_entry_id=entry_id,
        skip_queue_status_check=True,
        add_to_queue_if_no_staff=False,
        send_notification=False,
        ai_disabled=True,
        auto_commit=auto_commit,
        target_staff_id=target_staff_id,
    )


@pytest.mark.parametrize("direct", [False, True])
async def test_two_workers_only_assign_the_last_seat_once(
    live_reception, monkeypatch, direct
):
    state, _run = live_reception
    second = one_seat(state)
    first = (state.visitor.id, state.session.id, state.entry.id)
    project_id = state.project.id
    staff_id = state.staff.id
    engine = state.db.get_bind()
    chosen, holding, release = Barrier(2), Event(), Event()
    original_choose = transfer_service.assign_staff
    original_history = transfer_service._create_assignment_history

    async def same_snapshot(*args, **kwargs):
        result = await original_choose(*args, **kwargs)
        assert result.assigned_staff_id == staff_id
        chosen.wait(timeout=5)
        return result

    def hold_winner(*args, **kwargs):
        if kwargs["assigned_staff_id"] is not None:
            holding.set()
            assert release.wait(timeout=5)
        return original_history(*args, **kwargs)

    monkeypatch.setattr(transfer_service, "assign_staff", same_snapshot)
    monkeypatch.setattr(
        transfer_service, "_create_assignment_history", hold_winner
    )

    def worker(identities):
        with Session(engine) as db:
            result = asyncio.run(
                transfer(
                    db,
                    project_id,
                    identities,
                    target_staff_id=staff_id if direct else None,
                )
            )
            return result.success, result.assigned_staff_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, item) for item in (first, second)]
        try:
            assert holding.wait(timeout=5)
            done, _pending = wait(
                futures, timeout=3, return_when=FIRST_COMPLETED
            )
            assert len(done) == 1  # The other visitor is not blocked.
            assert next(iter(done)).result() == (not direct, None)
        finally:
            release.set()
        results = [future.result(timeout=5) for future in futures]
    assert sorted(identifier is not None for _, identifier in results) == [
        False,
        True,
    ]
    state.db.expire_all()
    sessions = (
        state.db.query(VisitorSession)
        .filter_by(
            project_id=project_id,
            staff_id=staff_id,
            status="open",
        )
        .all()
    )
    assert len(sessions) == 1
    assert state.complete.await_count == 0


async def test_unlimited_capacity_allows_parallel_assignments(
    live_reception, monkeypatch
):
    state, _run = live_reception
    second = one_seat(state)
    state.rule.max_concurrent_chats = None
    state.db.commit()
    first = (state.visitor.id, state.session.id, state.entry.id)
    engine, project_id, staff_id = (
        state.db.get_bind(),
        state.project.id,
        state.staff.id,
    )
    entered = Barrier(2)
    original = transfer_service._create_assignment_history

    def both_can_write(*args, **kwargs):
        assert kwargs["assigned_staff_id"] == staff_id
        entered.wait(timeout=5)
        return original(*args, **kwargs)

    monkeypatch.setattr(
        transfer_service, "_create_assignment_history", both_can_write
    )

    def worker(identities):
        with Session(engine) as db:
            result = asyncio.run(transfer(db, project_id, identities))
            return result.success, result.assigned_staff_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, item) for item in (first, second)]
        assert [future.result(timeout=8) for future in futures] == [
            (True, staff_id),
            (True, staff_id),
        ]


@pytest.mark.parametrize("finish", ["commit", "rollback"])
async def test_reserved_seat_is_released_by_the_owning_transaction(
    live_reception, finish
):
    state, _run = live_reception
    second = one_seat(state)
    first = (state.visitor.id, state.session.id, state.entry.id)
    result = await transfer(
        state.db, state.project.id, first, auto_commit=False
    )
    assert result.success and result.assigned_staff_id == state.staff.id
    with Session(state.db.get_bind()) as elsewhere:
        blocked = await transfer(elsewhere, state.project.id, second)
        assert blocked.success and blocked.assigned_staff_id is None
        if finish == "rollback":
            state.db.rollback()
        else:
            state.db.commit()
            state.session.status = "closed"
            state.db.commit()
        # Keep the first Session open: a session-scoped lock would still fail.
        retried = await transfer(elsewhere, state.project.id, second)
        assert retried.success
        assert retried.assigned_staff_id == state.staff.id


async def test_uncommitted_staff_update_does_not_block_another_choice(
    live_reception,
):
    state, run = live_reception
    with Session(state.db.get_bind()) as elsewhere:
        elsewhere.execute(
            update(Staff)
            .where(
                Staff.id == state.staff.id,
            )
            .values(service_paused=True)
        )
        result = await run("unchanged")
        assert result.success, result.message
        assert result.assigned_staff_id == state.colleague.id
        elsewhere.rollback()
