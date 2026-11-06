"""A decision in flight cannot revive cancelled or reassigned reception."""

from datetime import datetime, timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.config import settings
from app.models import (
    ManualServiceRequest,
    AIProvider,
    AssignmentSource,
    Platform,
    Project,
    Tag,
    Visitor,
    VisitorAssignmentHistory,
    VisitorSession,
    VisitorTag,
    VisitorWaitingQueue,
)
from app.services import (
    company_entitlements,
    company_membership,
    human_handoff_service,
    queue_lifecycle,
    transfer_service,
)
from app.services.ai_client import ai_client
from tests import test_staff_assignment_capacity as capacity_checks
from tests import test_human_handoff_service as handoff_checks

queue_db = capacity_checks.queue_db
reception = capacity_checks.reception
pytestmark = pytest.mark.asyncio


def completion(staff_id):
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "selected_staff_id": str(staff_id),
                            "reasoning": "Synthetic choice",
                        }
                    )
                }
            }
        ]
    }


@pytest.mark.parametrize(
    "action",
    [
        "cancel",
        "claim",
        "expire",
        "requeue",
        "retire",
        "platform",
        "project",
        "message",
    ],
)
async def test_action_during_model_wait_wins(reception, monkeypatch, action):
    db, visitor, session, entry, staff, _rule = reception
    identifiers = visitor.id, visitor.project_id, session.id, entry.id
    replacement = []

    async def respond(**_kwargs):
        if action == "claim":
            claimed = await transfer_service.transfer_to_staff(
                db,
                identifiers[0],
                identifiers[1],
                target_staff_id=staff[1].id,
                session_id=identifiers[2],
                expected_queue_entry_id=identifiers[3],
                send_notification=False,
                ai_disabled=True,
            )
            assert claimed.success, claimed.message
        elif action in {"cancel", "requeue"}:
            assert queue_lifecycle.cancel_entry(
                db, entry.id, visitor.project_id
            )
            if action == "requeue":
                current = VisitorSession(
                    project_id=visitor.project_id,
                    visitor_id=visitor.id,
                    platform_id=visitor.platform_id,
                    status="open",
                    created_at=datetime.utcnow() + timedelta(seconds=1),
                )
                db.add(current)
                db.flush()
                new_entry = VisitorWaitingQueue(
                    project_id=visitor.project_id,
                    visitor_id=visitor.id,
                    session_id=current.id,
                    status="waiting",
                    expired_at=datetime.utcnow() + timedelta(minutes=5),
                )
                visitor.set_status_queued()
                db.add(new_entry)
                db.flush()
                replacement.append(new_entry.id)
            db.commit()
        else:
            if action == "expire":
                entry.expired_at = datetime.utcnow() - timedelta(seconds=1)
            elif action == "retire":
                visitor.access_retired_at = datetime.utcnow()
            elif action == "platform":
                db.get(Platform, visitor.platform_id).is_active = False
            elif action == "project":
                db.get(
                    Project, visitor.project_id
                ).deleted_at = datetime.utcnow()
            else:
                visitor.nickname = "Updated during the wait"
                session.message_count += 1
                session.updated_at = datetime.utcnow()
            db.commit()
        return completion(staff[0].id)

    model = AsyncMock(side_effect=respond)
    monkeypatch.setattr(ai_client, "chat_completions", model)
    result = await transfer_service.transfer_to_staff(
        db,
        identifiers[0],
        identifiers[1],
        session_id=identifiers[2],
        expected_queue_entry_id=identifiers[3],
        skip_queue_status_check=True,
        add_to_queue_if_no_staff=False,
        send_notification=False,
        ai_disabled=True,
    )
    model.assert_awaited_once()
    db.expire_all()
    if action == "message":
        assert result.success, result.message
        assert session.staff_id == staff[0].id
        assert visitor.nickname == "Updated during the wait"
        assert session.message_count == 1
    else:
        assert not result.success
        assert db.query(VisitorAssignmentHistory).count() == (
            action == "claim"
        )
        if action == "claim":
            assert entry.status == "assigned"
            assert session.staff_id == staff[1].id
            assert visitor.service_status == "active"
        elif action in {"cancel", "requeue"}:
            assert entry.status == "cancelled"
            assert session.status == "closed"
            if action == "requeue":
                current = db.get(VisitorWaitingQueue, replacement[0])
                assert current.status == "waiting"
                assert visitor.service_status == "queued"
        else:
            assert entry.status == "waiting"
            assert session.staff_id is None


async def test_no_session_or_attempt_is_written_before_the_wait(
    reception, monkeypatch
):
    db, visitor, session, entry, staff, _rule = reception
    attempts = []

    async def queued_response(**_kwargs):
        attempts.append(entry.last_attempt_at)
        return completion(staff[0].id)

    monkeypatch.setattr(ai_client, "chat_completions", queued_response)
    result = await transfer_service.transfer_to_staff(
        db,
        visitor.id,
        visitor.project_id,
        session_id=session.id,
        expected_queue_entry_id=entry.id,
        skip_queue_status_check=True,
        send_notification=False,
    )
    assert result.success, result.message
    assert attempts == [None]
    assert entry.last_attempt_at is not None

    new_visitor = Visitor(
        project_id=visitor.project_id,
        platform_id=visitor.platform_id,
        platform_open_id=uuid4().hex,
        service_status="new",
    )
    db.add(new_visitor)
    db.commit()
    observed = []

    async def new_response(**_kwargs):
        observed.append(
            db.query(VisitorSession)
            .filter_by(
                visitor_id=new_visitor.id,
            )
            .count()
        )
        return completion(staff[1].id)

    # The first customer occupied the other staff member's single seat.
    _rule.max_concurrent_chats = 3
    db.commit()
    monkeypatch.setattr(ai_client, "chat_completions", new_response)
    result = await transfer_service.transfer_to_staff(
        db,
        new_visitor.id,
        new_visitor.project_id,
        send_notification=False,
    )
    assert result.success, result.message
    assert observed == [0]


@pytest.mark.parametrize("invalid", ["foreign", "closed"])
async def test_requested_session_must_still_belong_to_this_reception(
    reception, invalid
):
    db, visitor, session, _entry, staff, _rule = reception
    requested = session
    if invalid == "foreign":
        other = Visitor(
            project_id=visitor.project_id,
            platform_id=visitor.platform_id,
            platform_open_id=uuid4().hex,
        )
        db.add(other)
        db.flush()
        requested = VisitorSession(
            project_id=visitor.project_id,
            platform_id=visitor.platform_id,
            visitor_id=other.id,
            status="open",
        )
        db.add(requested)
    else:
        session.status = "closed"
    db.commit()
    result = await transfer_service.transfer_to_staff(
        db,
        visitor.id,
        visitor.project_id,
        target_staff_id=staff[0].id,
        session_id=requested.id,
        send_notification=False,
    )
    assert not result.success
    assert session.staff_id is None and requested.staff_id is None
    assert db.query(VisitorAssignmentHistory).count() == 0


@pytest.mark.parametrize("autoflush", [False, True])
async def test_owner_can_roll_back_all_transfer_writes(
    reception, monkeypatch, autoflush
):
    db, visitor, session, entry, staff, _rule = reception
    project = db.get(Project, visitor.project_id)
    visitor_id, project_id, session_id, entry_id, staff_id = (
        visitor.id,
        visitor.project_id,
        session.id,
        entry.id,
        staff[0].id,
    )
    old_name = project.name
    project.name = "Pending caller-owned update"
    db.autoflush = autoflush
    observed = []

    async def respond(**_kwargs):
        observed.append(
            db.query(Project.name).filter_by(id=project.id).scalar()
        )
        return completion(staff_id)

    monkeypatch.setattr(ai_client, "chat_completions", respond)
    result = await transfer_service.transfer_to_staff(
        db,
        visitor_id,
        project_id,
        session_id=session_id,
        expected_queue_entry_id=entry_id,
        auto_commit=False,
        skip_queue_status_check=True,
        send_notification=False,
    )
    assert result.success, result.message
    assert observed == [old_name]
    db.rollback()
    assert project.name == old_name
    assert entry.status == "waiting" and session.staff_id is None
    assert db.query(VisitorAssignmentHistory).count() == 0


async def test_company_write_lock_is_deferred_until_after_the_model(
    reception, monkeypatch
):
    db, visitor, session, entry, staff, _rule = reception
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", True)
    events = []
    monkeypatch.setattr(
        company_membership, "lock_company", lambda *_: events.append("lock")
    )
    monkeypatch.setattr(
        company_entitlements,
        "require_human_service",
        lambda *_: events.append("gate"),
    )

    async def respond(**_kwargs):
        events.append("model")
        return completion(staff[0].id)

    monkeypatch.setattr(ai_client, "chat_completions", respond)
    result = await transfer_service.transfer_to_staff(
        db,
        visitor.id,
        visitor.project_id,
        session_id=session.id,
        expected_queue_entry_id=entry.id,
        skip_queue_status_check=True,
        send_notification=False,
    )
    assert result.success, result.message
    assert events == ["gate", "model", "lock", "gate"]


async def test_handoff_persists_stop_ai_before_awaiting_assignment(
    monkeypatch,
):
    monkeypatch.setattr(settings, "CUSTOMER_TURNS_ENABLED", False)
    project = SimpleNamespace(id=uuid4())
    visitor = SimpleNamespace(
        id=uuid4(),
        project_id=project.id,
        platform_id=uuid4(),
        service_status="new",
        is_unassigned=True,
        ai_disabled=False,
        service_mode="auto",
    )
    db = handoff_checks._FakeDB(
        {
            Tag: SimpleNamespace(id=uuid4(), deleted_at=None),
            VisitorTag: SimpleNamespace(deleted_at=None),
        }
    )

    async def assigned(**_kwargs):
        assert db.commits == 1
        assert visitor.ai_disabled and visitor.service_mode == "manual"
        assert (
            next(
                value
                for value in db.added
                if isinstance(value, ManualServiceRequest)
            ).status
            == "pending"
        )
        return SimpleNamespace(
            success=True,
            assigned_staff_id=None,
            session=None,
            waiting_queue=SimpleNamespace(
                id=uuid4(),
                status="waiting",
                position=1,
                priority=0,
                channel_id=None,
                channel_type=None,
            ),
            message="Pending manual reception",
        )

    monkeypatch.setattr(human_handoff_service, "transfer_to_staff", assigned)
    monkeypatch.setattr(
        human_handoff_service, "notify_visitor_profile_updated", AsyncMock()
    )
    monkeypatch.setattr(
        human_handoff_service, "deliver_handoff_notice",
        AsyncMock(return_value="sent"),
    )
    result = await human_handoff_service.request_human_handoff(
        db=db,
        project=project,
        visitor=visitor,
        reason="Synthetic handoff",
    )
    assert result["notification_status"] == "submitted"


async def test_configuration_is_checked_again_when_final_locks_are_taken(
    reception, monkeypatch
):
    db, visitor, session, entry, staff, rule = reception
    rule.max_concurrent_chats = 3
    capacity_checks.occupy(db, visitor, staff[0])
    db.commit()
    original = transfer_service.assign_staff

    async def choose(*args, **kwargs):
        result = await original(*args, **kwargs)
        db.query(AIProvider).filter_by(
            id=rule.ai_provider_id
        ).one().is_active = False
        db.commit()
        return result

    monkeypatch.setattr(transfer_service, "assign_staff", choose)
    model = AsyncMock(return_value=completion(staff[0].id))
    monkeypatch.setattr(ai_client, "chat_completions", model)
    result = await transfer_service.transfer_to_staff(
        db,
        visitor.id,
        visitor.project_id,
        session_id=session.id,
        expected_queue_entry_id=entry.id,
        skip_queue_status_check=True,
        add_to_queue_if_no_staff=False,
        send_notification=False,
    )
    assert result.success, result.message
    model.assert_awaited_once()
    history = db.query(VisitorAssignmentHistory).one()
    assert history.assigned_staff_id == staff[1].id
    assert history.llm_response is None
    assert "configuration changed" in history.reasoning.lower()


@pytest.mark.parametrize("queued_flag", [False, True])
async def test_background_retry_preserves_current_assisted_reception(
    reception, monkeypatch, queued_flag
):
    db, visitor, session, entry, staff, _rule = reception
    visitor.service_mode = "assist"
    visitor.ai_disabled = True
    visitor.humanization_skill_enabled = True
    entry.ai_disabled = queued_flag
    db.commit()
    monkeypatch.setattr(
        ai_client,
        "chat_completions",
        AsyncMock(return_value=completion(staff[0].id)),
    )
    result = await transfer_service.transfer_to_staff(
        db,
        visitor.id,
        visitor.project_id,
        source=AssignmentSource.RULE,
        session_id=session.id,
        expected_queue_entry_id=entry.id,
        skip_queue_status_check=True,
        add_to_queue_if_no_staff=False,
        ai_disabled=entry.ai_disabled,
        send_notification=False,
    )
    assert result.success, result.message
    assert visitor.service_mode == "assist"
    assert visitor.ai_disabled and visitor.humanization_skill_enabled
