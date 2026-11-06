"""A durable manual request must survive losing its dispatcher process."""

import asyncio
import importlib
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.schema import CreateTable

from app.core.config import settings
from app.models import (
    ManualServiceRequest,
    Platform,
    Project,
    VisitorSession,
    VisitorWaitingQueue,
)
from app.services import human_handoff_service
from app.services.ai_client import ai_client
from tests import test_staff_assignment_capacity as capacity_checks

queue_db = capacity_checks.queue_db
reception = capacity_checks.reception
pytestmark = pytest.mark.asyncio
SOURCE_ID = "synthetic-interrupted-manual-request"


@pytest.fixture
def handoff(reception, monkeypatch):
    db, visitor, session, entry, staff, rule = reception
    # SQLite represents JSONB as JSON; keep its default dialect-compatible.
    ddl = str(CreateTable(ManualServiceRequest.__table__).compile(
        dialect=db.get_bind().dialect,
    )).replace("'{}'::jsonb", "'{}'")
    db.connection().exec_driver_sql(ddl)
    visitor.service_status = "new"
    visitor.ai_disabled = False
    visitor.service_mode = "auto"
    session.status = "closed"
    entry.status = "cancelled"
    db.commit()
    monkeypatch.setattr(settings, "CUSTOMER_TURNS_ENABLED", False)
    monkeypatch.setattr(
        human_handoff_service, "ensure_manual_service_tag", Mock()
    )
    monkeypatch.setattr(
        human_handoff_service, "notify_visitor_profile_updated", AsyncMock()
    )
    monkeypatch.setattr(human_handoff_service, "_notify_handoff", AsyncMock())
    monkeypatch.setattr(ai_client, "chat_completions", AsyncMock())
    return reception


async def interrupt(handoff, monkeypatch):
    db, visitor, *_ = handoff
    project = db.get(Project, visitor.project_id)
    with monkeypatch.context() as scope:
        scope.setattr(
            human_handoff_service, "transfer_to_staff",
            AsyncMock(side_effect=asyncio.CancelledError),
        )
        with pytest.raises(asyncio.CancelledError):
            await human_handoff_service.request_human_handoff(
                db=db, project=project, visitor=visitor,
                reason="转人工", source_message_id=SOURCE_ID,
                strict_source_receipt=True,
            )
    db.expire_all()
    request = db.query(ManualServiceRequest).one()
    return request


def recovery():
    return importlib.import_module("app.services.handoff_dispatch_recovery")


async def test_acceptance_persists_a_recoverable_reception_binding(
    handoff, monkeypatch,
):
    db, visitor, session, *_ = handoff
    request = await interrupt(handoff, monkeypatch)
    assert request.status == "pending"
    binding = request.request_metadata.get("handoff_dispatch_pending")
    assert isinstance(binding, dict), "Accepted request lacks recovery binding"
    assert binding["platform_id"] == str(visitor.platform_id)
    assert binding["session_id"] == str(session.id)
    assert binding["session_status"] == "closed"
    assert visitor.ai_disabled and visitor.service_mode == "manual"
    assert db.query(VisitorWaitingQueue).filter_by(
        status="waiting",
    ).count() == 0


async def age(db, request):
    request.updated_at = datetime.utcnow() - timedelta(minutes=10)
    db.commit()


async def test_interrupted_request_enters_existing_queue_once(
    handoff, monkeypatch,
):
    db, visitor, *_ = handoff
    request = await interrupt(handoff, monkeypatch)
    await age(db, request)
    outcome = await recovery().recover_pending_handoffs(db)
    assert outcome.recovered == 1 and outcome.cancelled == 0
    db.expire_all()
    queue = db.query(VisitorWaitingQueue).filter_by(status="waiting").one()
    assert queue.visitor_id == visitor.id and queue.ai_disabled
    assert visitor.service_status == "queued" and visitor.ai_disabled
    assert db.get(VisitorSession, queue.session_id).status == "open"
    saved = db.get(ManualServiceRequest, request.id)
    receipt = saved.request_metadata["handoff_receipt"]
    assert receipt["entry_id"] == str(queue.id)
    assert saved.request_metadata["notification_status"] == "unknown"
    repeated = await recovery().recover_pending_handoffs(db)
    assert repeated.checked == 0
    assert db.query(VisitorWaitingQueue).filter_by(
        status="waiting",
    ).count() == 1
    result = await human_handoff_service.request_human_handoff(
        db=db, project=db.get(Project, visitor.project_id), visitor=visitor,
        reason="转人工", source_message_id=SOURCE_ID,
        strict_source_receipt=True,
    )
    assert result["request_id"] == str(request.id)
    assert result["status"] == "waiting"
    ai_client.chat_completions.assert_not_awaited()
    human_handoff_service._notify_handoff.assert_not_awaited()


@pytest.mark.parametrize("status", ["active", "queued"])
async def test_assignment_committed_before_crash_is_reconciled(
    handoff, monkeypatch, status,
):
    db, visitor, _old_session, _old_entry, staff, _rule = handoff
    request = await interrupt(handoff, monkeypatch)
    session = VisitorSession(
        visitor_id=visitor.id, project_id=visitor.project_id,
        platform_id=visitor.platform_id, status="open",
        staff_id=staff[0].id if status == "active" else None,
        created_at=datetime.utcnow() + timedelta(seconds=1),
    )
    db.add(session)
    db.flush()
    if status == "queued":
        db.add(VisitorWaitingQueue(
            project_id=visitor.project_id, visitor_id=visitor.id,
            session_id=session.id, status="waiting", ai_disabled=True,
            expired_at=datetime.utcnow() + timedelta(minutes=5),
        ))
    visitor.service_status = status
    await age(db, request)
    outcome = await recovery().recover_pending_handoffs(db)
    assert outcome.recovered == 1
    assert db.query(VisitorSession).count() == 2
    assert request.request_metadata["handoff_receipt"]["status"] == (
        "active" if status == "active" else "waiting"
    )
    assert request.status == (
        "in_progress" if status == "active" else "pending"
    )
    ai_client.chat_completions.assert_not_awaited()


@pytest.mark.parametrize(
    "change",
    ["resume_ai", "assist", "retire", "delete", "platform", "archive",
     "new_session", "closed", "expired_queue"],
)
async def test_recovery_never_resurrects_changed_reception(
    handoff, monkeypatch, change,
):
    db, visitor, _session, *_ = handoff
    request = await interrupt(handoff, monkeypatch)
    if change == "resume_ai":
        visitor.ai_disabled = False
    elif change == "assist":
        visitor.service_mode = "assist"
    elif change == "retire":
        visitor.access_retired_at = datetime.utcnow()
    elif change == "delete":
        visitor.deleted_at = datetime.utcnow()
    elif change == "platform":
        db.get(Platform, visitor.platform_id).is_active = False
    elif change == "archive":
        db.get(Project, visitor.project_id).deleted_at = datetime.utcnow()
    elif change in {"new_session", "expired_queue"}:
        session = VisitorSession(
            id=uuid4(), visitor_id=visitor.id, project_id=visitor.project_id,
            platform_id=visitor.platform_id, status="open",
            created_at=datetime.utcnow() + timedelta(seconds=1),
        )
        db.add(session)
        if change == "expired_queue":
            visitor.service_status = "queued"
            db.add(VisitorWaitingQueue(
                project_id=visitor.project_id, visitor_id=visitor.id,
                session_id=session.id, status="waiting", ai_disabled=True,
                expired_at=datetime.utcnow() - timedelta(seconds=1),
            ))
    elif change == "closed":
        visitor.service_status = "closed"
    await age(db, request)
    before = db.query(VisitorWaitingQueue).count()
    outcome = await recovery().recover_pending_handoffs(db)
    assert outcome.recovered == 0 and outcome.cancelled == 1
    assert db.query(VisitorWaitingQueue).count() == before
    assert request.request_metadata["handoff_receipt"]["status"] == "cancelled"
    ai_client.chat_completions.assert_not_awaited()


async def test_recent_and_legacy_requests_are_not_automatically_replayed(
    handoff, monkeypatch,
):
    db, *_ = handoff
    request = await interrupt(handoff, monkeypatch)
    outcome = await recovery().recover_pending_handoffs(db)
    assert outcome.checked == 0
    request.request_metadata = {}
    await age(db, request)
    outcome = await recovery().recover_pending_handoffs(db)
    assert outcome.checked == 0
    assert request.status == "pending"


async def test_finished_inline_handoff_clears_its_recovery_marker(
    handoff, monkeypatch,
):
    db, visitor, *_ = handoff
    monkeypatch.setattr(ai_client, "chat_completions", AsyncMock(
        side_effect=RuntimeError("synthetic provider unavailable"),
    ))
    result = await human_handoff_service.request_human_handoff(
        db=db, project=db.get(Project, visitor.project_id), visitor=visitor,
        reason="转人工", source_message_id=SOURCE_ID,
        strict_source_receipt=True,
    )
    assert result["status"] == "active"
    request = db.query(ManualServiceRequest).one()
    assert "handoff_dispatch_pending" not in request.request_metadata


async def test_recovery_wins_without_late_dispatch_overwriting_its_receipt(
    handoff, monkeypatch,
):
    db, visitor, *_ = handoff

    async def complete(**_kwargs):
        request = db.query(ManualServiceRequest).one()
        await age(db, request)
        result = await recovery().recover_pending_handoffs(db)
        assert result.recovered == 1
        return {"choices": []}

    model = AsyncMock(side_effect=complete)
    monkeypatch.setattr(ai_client, "chat_completions", model)
    result = await human_handoff_service.request_human_handoff(
        db=db, project=db.get(Project, visitor.project_id), visitor=visitor,
        reason="转人工", source_message_id=SOURCE_ID,
        strict_source_receipt=True,
    )
    assert result["status"] == "waiting"
    assert result["notification_status"] == "unknown"
    request = db.query(ManualServiceRequest).one()
    assert request.status == "pending"
    assert "handoff_dispatch_pending" not in request.request_metadata
    assert db.query(VisitorWaitingQueue).filter_by(
        status="waiting",
    ).count() == 1
    model.assert_awaited_once()
    human_handoff_service._notify_handoff.assert_not_awaited()


async def test_recovered_queue_can_be_assigned_by_existing_processor(
    handoff, monkeypatch,
):
    from app.services.transfer_service import transfer_to_staff

    db, visitor, _session, _entry, _staff, rule = handoff
    request = await interrupt(handoff, monkeypatch)
    await age(db, request)
    await recovery().recover_pending_handoffs(db)
    entry = db.query(VisitorWaitingQueue).filter_by(status="waiting").one()
    rule.llm_assignment_enabled = False
    db.commit()
    result = await transfer_to_staff(
        db, visitor.id, visitor.project_id, session_id=entry.session_id,
        expected_queue_entry_id=entry.id, skip_queue_status_check=True,
        add_to_queue_if_no_staff=False, ai_disabled=entry.ai_disabled,
        send_notification=False,
    )
    assert result.success and result.assigned_staff_id is not None
    assert entry.status == "assigned" and visitor.service_status == "active"
    assert visitor.ai_disabled and visitor.service_mode == "manual"
    ai_client.chat_completions.assert_not_awaited()


async def test_recovered_session_sorts_after_equal_database_timestamps(
    handoff, monkeypatch,
):
    from app.services.visitor_mutation_access import timestamp

    db, _visitor, old, *_ = handoff
    request = await interrupt(handoff, monkeypatch)
    old.created_at = datetime.utcnow() + timedelta(seconds=1)
    await age(db, request)
    result = await recovery().recover_pending_handoffs(db)
    assert result.recovered == 1
    entry = db.query(VisitorWaitingQueue).filter_by(status="waiting").one()
    session = db.get(VisitorSession, entry.session_id)
    assert timestamp(session.created_at) > timestamp(old.created_at)


@pytest.mark.parametrize("version", [2, "future"])
async def test_unknown_marker_versions_are_left_for_their_owner(
    handoff, monkeypatch, version,
):
    db, *_ = handoff
    request = await interrupt(handoff, monkeypatch)
    request.request_metadata = {
        **request.request_metadata,
        "handoff_dispatch_pending": {"version": version},
    }
    await age(db, request)
    result = await recovery().recover_pending_handoffs(db)
    assert result.checked == 0 and request.status == "pending"
    assert request.request_metadata["handoff_dispatch_pending"]["version"] == (
        version
    )


async def test_cursor_visits_later_requests_after_a_failed_recovery(
    handoff, monkeypatch,
):
    db, visitor, *_ = handoff
    request = await interrupt(handoff, monkeypatch)
    await age(db, request)
    later = ManualServiceRequest(
        id=uuid4(), project_id=visitor.project_id, visitor_id=visitor.id,
        reason="转人工", status="pending",
        request_metadata=dict(request.request_metadata),
        updated_at=datetime.utcnow() - timedelta(minutes=10),
    )
    db.add(later)
    db.commit()
    module = recovery()
    with monkeypatch.context() as scope:
        scope.setattr(module, "_recover_one", AsyncMock(
            side_effect=RuntimeError("synthetic failure"),
        ))
        first = await module.recover_pending_handoffs(db, limit=1)
    second = await module.recover_pending_handoffs(
        db, after_request_id=first.next_cursor, limit=1,
    )
    assert first.failed == 1 and first.next_cursor is not None
    assert second.next_cursor is not None
    assert second.next_cursor != first.next_cursor
    assert second.recovered == 1


async def test_existing_recovery_loop_runs_handoffs_and_keeps_its_cursor(
    monkeypatch,
):
    from app.tasks import recover_staff_delivery_history as task

    context = MagicMock()
    monkeypatch.setattr(task, "SessionLocal", Mock(return_value=context))
    for name in (
        "recover_stale_external_deliveries", "recover_pending_history",
        "recover_pending_training", "recover_session_commands",
        "recover_channel_effects",
    ):
        monkeypatch.setattr(task, name, AsyncMock())
    cursor = uuid4()
    recover = AsyncMock(return_value=recovery().HandoffRecoverySweep(
        next_cursor=cursor,
    ))
    monkeypatch.setattr(task, "recover_pending_handoffs", recover)
    monkeypatch.setattr(task.asyncio, "sleep", AsyncMock(
        side_effect=[None, asyncio.CancelledError],
    ))
    with pytest.raises(asyncio.CancelledError):
        await task._recover_loop()
    assert recover.await_count == 2
    assert recover.await_args_list[0].kwargs["after_request_id"] is None
    assert recover.await_args_list[1].kwargs["after_request_id"] == cursor
