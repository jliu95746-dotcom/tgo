"""Real concurrent configuration writes while the dispatcher awaits HTTP."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import (
    AIProvider,
    Staff,
    Visitor,
    VisitorAssignmentHistory,
    VisitorAssignmentRule,
    VisitorSession,
    VisitorWaitingQueue,
)
from app.services import queue_events, transfer_service
from app.services.ai_client import ai_client
from tests import test_authentication_boundaries_postgres as postgres_checks

postgres_engine = postgres_checks.postgres_engine
identity = postgres_checks.identity
PRIVATE = "synthetic-private-dispatcher-content"
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not postgres_checks.TEST_URL or not postgres_checks.TEST_SERVER,
        reason="Disposable PostgreSQL and controlled AI HTTP service required",
    ),
]


@pytest.fixture
def live_dispatch(identity, monkeypatch):
    db, project, staff, platform = identity
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", False)
    monkeypatch.setattr(transfer_service, "_add_staff_to_channel", AsyncMock(return_value=None))
    monkeypatch.setattr(
        transfer_service, "notify_visitor_profile_updated", AsyncMock()
    )
    monkeypatch.setattr(queue_events, "publish_queue_updates", AsyncMock())
    complete = AsyncMock(wraps=ai_client.chat_completions)
    monkeypatch.setattr(ai_client, "chat_completions", complete)
    staff.status = "online"
    colleague = Staff(
        project_id=project.id,
        username="dispatch-" + uuid4().hex,
        password_hash="disabled-fixture-login",
        role="user",
        status="online",
    )
    provider = AIProvider(
        project_id=project.id,
        provider="openai",
        name="Synthetic dispatcher",
        api_key="synthetic-unused-key",
        default_model="fixture-model",
    )
    visitor = Visitor(
        project_id=project.id,
        platform_id=platform.id,
        platform_open_id=uuid4().hex,
        service_status="queued",
        ai_disabled=True,
    )
    db.add_all([colleague, provider, visitor])
    db.flush()
    rule = VisitorAssignmentRule(
        project_id=project.id,
        ai_provider_id=provider.id,
        llm_assignment_enabled=True,
        model="fixture-model",
    )
    session = VisitorSession(
        project_id=project.id,
        platform_id=platform.id,
        visitor_id=visitor.id,
        status="open",
    )
    db.add_all([rule, session])
    db.flush()
    entry = VisitorWaitingQueue(
        project_id=project.id,
        visitor_id=visitor.id,
        session_id=session.id,
        status="waiting",
        entered_at=datetime.utcnow(),
        expired_at=datetime.utcnow() + timedelta(minutes=5),
        ai_disabled=True,
    )
    db.add(entry)
    # Make the model's choice deliberately different from load balancing.
    for _ in range(3):
        extra = Visitor(
            project_id=project.id,
            platform_id=platform.id,
            platform_open_id=uuid4().hex,
        )
        db.add(extra)
        db.flush()
        db.add(
            VisitorSession(
                project_id=project.id,
                platform_id=platform.id,
                visitor_id=extra.id,
                staff_id=staff.id,
                status="open",
            )
        )
    db.commit()
    state = SimpleNamespace(
        db=db,
        project=project,
        staff=staff,
        colleague=colleague,
        provider=provider,
        rule=rule,
        visitor=visitor,
        session=session,
        entry=entry,
        complete=complete,
    )

    async def run(case):
        return await transfer_service.transfer_to_staff(
            db,
            visitor.id,
            project.id,
            session_id=session.id,
            expected_queue_entry_id=entry.id,
            skip_queue_status_check=True,
            add_to_queue_if_no_staff=False,
            send_notification=False,
            ai_disabled=True,
            visitor_message="SCENARIO:guard_" + case,
        )

    return state, run


@pytest.mark.parametrize(
    "case",
    [
        "unchanged",
        "provider_disabled",
        "provider_deleted",
        "provider_removed",
        "provider_model",
        "provider_aba",
        "rule_disabled",
        "rule_model",
        "rule_prompt",
        "rule_provider",
        "rule_removed",
        "rule_aba",
        "rule_hours",
    ],
)
async def test_committed_remote_change_discards_old_decision(
    live_dispatch, caplog, case
):
    state, run = live_dispatch
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
    if case == "unchanged":
        assert history.assigned_staff_id == state.staff.id
        assert history.reasoning == PRIVATE
        assert history.llm_response is not None
    else:
        assert "configuration changed" in history.reasoning.lower()
        assert history.llm_response is None
        assert PRIVATE not in history.reasoning
        if case == "rule_hours":
            assert history.assigned_staff_id is None
            assert not history.candidate_staff_ids
            assert state.entry.status == "waiting"
            assert state.visitor.service_status == "queued"
            assert state.session.staff_id is None
        else:
            assert history.assigned_staff_id == state.colleague.id
    if case != "rule_hours":
        assert state.entry.status == "assigned"
        assert state.session.staff_id == history.assigned_staff_id
        assert state.visitor.service_status == "active"
    if case == "rule_removed":
        assert history.assignment_rule_id is None
    assert PRIVATE not in repr([vars(record) for record in caplog.records])


@pytest.mark.parametrize("target", ["provider", "rule"])
async def test_validation_locks_end_at_the_owning_commit(
    live_dispatch, monkeypatch, target
):
    state, run = live_dispatch
    model = AIProvider if target == "provider" else VisitorAssignmentRule
    identifier = state.provider.id if target == "provider" else state.rule.id
    values = (
        {"name": "After request"}
        if target == "provider"
        else {"prompt": "After request"}
    )
    command = update(model).where(model.id == identifier).values(**values)
    original = transfer_service._create_assignment_history
    checked = []

    def history_with_lock_check(*args, **kwargs):
        with Session(state.db.get_bind()) as elsewhere:
            elsewhere.execute(text("SET LOCAL lock_timeout = '150ms'"))
            with pytest.raises(OperationalError) as blocked:
                elsewhere.execute(command)
            assert getattr(blocked.value.orig, "pgcode", None) == "55P03"
            elsewhere.rollback()
        checked.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(
        transfer_service, "_create_assignment_history", history_with_lock_check
    )
    result = await run("unchanged")
    assert result.success, result.message
    assert checked == [True]
    with Session(state.db.get_bind()) as elsewhere:
        elsewhere.execute(text("SET LOCAL lock_timeout = '500ms'"))
        elsewhere.execute(command)
        elsewhere.commit()
