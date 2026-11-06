"""Independent operators can act while the dispatcher awaits real HTTP."""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import (
    AIProvider,
    AssignmentSource,
    ManualServiceRequest,
    VisitorAssignmentHistory,
    VisitorWaitingQueue,
)
from app.services import human_handoff_service, transfer_service
from tests import test_staff_assignment_revalidation_postgres as guard_checks

postgres_engine = guard_checks.postgres_engine
identity = guard_checks.identity
live_dispatch = guard_checks.live_dispatch
pytestmark = guard_checks.pytestmark
PRIVATE = guard_checks.PRIVATE


@pytest.fixture
def waiting(live_dispatch):
    state, _run = live_dispatch
    state.visitor.name = "Synthetic wait target"
    state.rule.max_concurrent_chats = 4
    state.db.commit()

    async def run(case, *, auto_commit=True):
        return await transfer_service.transfer_to_staff(
            state.db,
            state.visitor.id,
            state.project.id,
            source=AssignmentSource.RULE,
            session_id=state.session.id,
            expected_queue_entry_id=state.entry.id,
            skip_queue_status_check=True,
            add_to_queue_if_no_staff=False,
            send_notification=False,
            ai_disabled=True,
            visitor_message="SCENARIO:wait_" + case,
            auto_commit=auto_commit,
        )

    return state, run


@pytest.mark.parametrize(
    "case",
    [
        "cancel",
        "claim",
        "expire",
        "requeue",
        "retire",
        "platform",
        "project",
        "mode",
        "message",
    ],
)
async def test_operator_commits_before_the_model_response(waiting, case):
    state, run = waiting
    result = await run(case)
    state.complete.assert_awaited_once()
    state.db.expire_all()
    history = (
        state.db.query(VisitorAssignmentHistory)
        .filter_by(
            visitor_id=state.visitor.id,
        )
        .all()
    )
    if case == "message":
        assert result.success, result.message
        assert result.assigned_staff_id == state.staff.id
        assert len(history) == 1 and history[0].reasoning == PRIVATE
        assert state.session.message_count == 1
        assert state.visitor.nickname == "Updated while waiting"
        return
    assert not result.success
    if case == "claim":
        assert len(history) == 1
        assert state.session.staff_id == state.colleague.id
        assert state.entry.status == "assigned"
        assert state.visitor.service_status == "active"
        assert history[0].model_used is None
    else:
        assert not history and state.session.staff_id is None
        if case in {"cancel", "requeue"}:
            assert state.entry.status == "cancelled"
            assert state.session.status == "closed"
        if case == "expire":
            assert state.entry.status == "expired"
            assert state.visitor.service_status == "closed"
        if case == "requeue":
            current = (
                state.db.query(VisitorWaitingQueue)
                .filter_by(
                    visitor_id=state.visitor.id,
                    status="waiting",
                )
                .one()
            )
            assert current.id != state.entry.id
            assert current.session_id != state.session.id
            assert state.visitor.service_status == "queued"
        if case == "mode":
            assert state.visitor.service_mode == "assist"
            assert state.visitor.humanization_skill_enabled
            retried = await run("probe")
            assert retried.success, retried.message
            assert state.visitor.service_mode == "assist"
            assert state.visitor.ai_disabled
            assert state.visitor.humanization_skill_enabled
            assert state.complete.await_count == 2


@pytest.mark.parametrize("billing", [False, True])
async def test_project_and_visitor_are_unlocked_during_http(
    waiting, monkeypatch, billing
):
    state, run = waiting
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", billing)
    result = await run("probe")
    assert result.success, result.message
    assert result.assigned_staff_id == state.staff.id
    assert result.assignment_history.reasoning == PRIVATE
    state.complete.assert_awaited_once()


async def test_handoff_stop_ai_is_committed_before_the_model(
    waiting, monkeypatch
):
    state, _run = waiting
    monkeypatch.setattr(settings, "CUSTOMER_TURNS_ENABLED", False)
    monkeypatch.setattr(
        human_handoff_service, "notify_visitor_profile_updated", AsyncMock()
    )
    monkeypatch.setattr(human_handoff_service, "_notify_handoff", AsyncMock())
    state.visitor.service_status = "new"
    state.visitor.ai_disabled = False
    state.visitor.service_mode = "auto"
    state.entry.status = "cancelled"
    state.session.status = "closed"
    state.db.commit()
    result = await human_handoff_service.request_human_handoff(
        db=state.db,
        project=state.project,
        visitor=state.visitor,
        reason="SCENARIO:wait_handoff_probe",
    )
    state.complete.assert_awaited_once()
    assert result["status"] == "active"
    state.db.expire_all()
    request = (
        state.db.query(ManualServiceRequest)
        .filter_by(
            visitor_id=state.visitor.id,
        )
        .one()
    )
    assert request.status == "in_progress"
    history = (
        state.db.query(VisitorAssignmentHistory)
        .filter_by(
            visitor_id=state.visitor.id,
        )
        .one()
    )
    assert history.reasoning == PRIVATE
    assert state.visitor.ai_disabled
    assert state.visitor.service_mode == "manual"


async def test_resource_revocation_between_read_and_final_locks(
    waiting, monkeypatch
):
    state, run = waiting
    original = transfer_service.assign_staff

    async def select(*args, **kwargs):
        result = await original(*args, **kwargs)
        with Session(state.db.get_bind()) as elsewhere:
            elsewhere.execute(text("SET LOCAL lock_timeout = '1000ms'"))
            elsewhere.execute(
                update(AIProvider)
                .where(
                    AIProvider.id == state.provider.id,
                )
                .values(is_active=False)
            )
            elsewhere.commit()
        return result

    monkeypatch.setattr(transfer_service, "assign_staff", select)
    result = await run("probe")
    assert result.success, result.message
    assert result.assigned_staff_id == state.colleague.id
    assert result.assignment_history.llm_response is None
    assert (
        "configuration changed" in result.assignment_history.reasoning.lower()
    )
    state.complete.assert_awaited_once()


async def test_caller_owns_commit_and_rollback_with_autoflush_enabled(waiting):
    state, _run = waiting
    project_id, visitor_id, session_id, entry_id = (
        state.project.id,
        state.visitor.id,
        state.session.id,
        state.entry.id,
    )
    old_name = state.project.name
    state.project.name = "Uncommitted owner change"
    state.db.autoflush = True
    result = await transfer_service.transfer_to_staff(
        state.db,
        visitor_id,
        project_id,
        session_id=session_id,
        expected_queue_entry_id=entry_id,
        skip_queue_status_check=True,
        add_to_queue_if_no_staff=False,
        send_notification=False,
        auto_commit=False,
        visitor_message="SCENARIO:wait_probe",
    )
    assert result.success, result.message
    assert result.assignment_history.reasoning == PRIVATE
    state.db.rollback()
    assert state.project.name == old_name
    assert state.entry.status == "waiting"
    assert state.session.staff_id is None
    assert (
        not state.db.query(VisitorAssignmentHistory)
        .filter_by(
            visitor_id=visitor_id,
        )
        .all()
    )
