"""Final assignment uses live staff eligibility and remaining capacity."""

from datetime import datetime, timedelta
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import update

from app.core.config import settings
from app.models import (
    AIProvider,
    Staff,
    Visitor,
    VisitorAssignmentHistory,
    VisitorAssignmentRule,
    VisitorSession,
)
from app.services import transfer_service
from app.services.ai_client import ai_client
from tests import test_queue_assignment as queue_checks

queue_db = queue_checks.queue_db
PRIVATE = "synthetic-private-seat-decision"
pytestmark = pytest.mark.asyncio


@pytest.fixture
def reception(queue_db, monkeypatch):
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", False)
    for model in (
        Staff,
        AIProvider,
        VisitorAssignmentRule,
        VisitorAssignmentHistory,
    ):
        model.__table__.create(queue_db.get_bind())
    visitor, session, entry = queue_checks.seed_queue(
        queue_db, expired_at=datetime.utcnow() + timedelta(minutes=5)
    )
    staff = [
        Staff(
            project_id=visitor.project_id,
            username=uuid4().hex,
            password_hash="disabled-fixture-login",
            role="user",
            status="online",
        )
        for _ in range(2)
    ]
    provider = AIProvider(
        project_id=visitor.project_id,
        provider="openai",
        name="Synthetic dispatcher",
        api_key="synthetic-unused-key",
        default_model="fixture-model",
    )
    queue_db.add_all([*staff, provider])
    queue_db.flush()
    rule = VisitorAssignmentRule(
        project_id=visitor.project_id,
        ai_provider_id=provider.id,
        model="fixture-model",
        llm_assignment_enabled=True,
        max_concurrent_chats=1,
    )
    queue_db.add(rule)
    queue_db.commit()
    monkeypatch.setattr(transfer_service, "_add_staff_to_channel", AsyncMock(return_value=None))
    return queue_db, visitor, session, entry, staff, rule


def occupy(db, visitor, staff):
    another = Visitor(
        project_id=visitor.project_id,
        platform_id=visitor.platform_id,
        platform_open_id=uuid4().hex,
    )
    db.add(another)
    db.flush()
    db.add(
        VisitorSession(
            visitor_id=another.id,
            project_id=visitor.project_id,
            platform_id=visitor.platform_id,
            staff_id=staff.id,
            status="open",
        )
    )
    db.flush()


@pytest.mark.parametrize(
    "change",
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
async def test_live_staff_change_after_the_model_wait(
    reception, monkeypatch, change
):
    db, visitor, session, entry, staff, _rule = reception
    selected = staff[0].id

    async def complete(**_kwargs):
        changes = {
            "paused": {"service_paused": True},
            "disabled": {"account_enabled": False},
            "inactive": {"is_active": False},
            "deleted": {"deleted_at": datetime.utcnow()},
            "agent": {"role": "agent"},
            "offline": {"status": "offline"},
        }
        if change in changes:
            db.execute(
                update(Staff)
                .where(Staff.id == selected)
                .values(**changes[change])
                .execution_options(synchronize_session=False)
            )
        elif change == "all_paused":
            db.execute(
                update(Staff)
                .where(Staff.project_id == visitor.project_id)
                .values(service_paused=True)
                .execution_options(synchronize_session=False)
            )
        elif change == "capacity":
            occupy(db, visitor, staff[0])
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "selected_staff_id": str(selected),
                                "reasoning": PRIVATE,
                            }
                        )
                    }
                }
            ]
        }

    model = AsyncMock(side_effect=complete)
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
        ai_disabled=True,
    )
    assert result.success, result.message
    model.assert_awaited_once()
    history = db.query(VisitorAssignmentHistory).one()
    if change in {"unchanged", "offline"}:
        assert history.assigned_staff_id == selected
        assert history.reasoning == PRIVATE
    else:
        assert history.llm_response is None
        assert PRIVATE not in history.reasoning
        assert history.assigned_staff_id != selected
        if change == "all_paused":
            assert not history.candidate_staff_ids
            assert history.assigned_staff_id is None
            assert entry.status == "waiting"
            assert visitor.service_status == "queued"
            assert session.staff_id is None
        else:
            assert history.assigned_staff_id == staff[1].id
            assert str(selected) not in history.candidate_staff_ids
    if change != "all_paused":
        assert entry.status == "assigned"
        assert visitor.service_status == "active"
        assert session.staff_id == history.assigned_staff_id
    assert visitor.service_mode == "manual" and visitor.ai_disabled


@pytest.mark.parametrize("change", ["paused", "inactive", "capacity"])
async def test_direct_target_is_not_silently_reassigned(reception, change):
    db, visitor, session, entry, staff, _rule = reception
    if change == "capacity":
        occupy(db, visitor, staff[0])
    else:
        setattr(
            staff[0],
            "service_paused" if change == "paused" else "is_active",
            change == "paused",
        )
    db.commit()
    result = await transfer_service.transfer_to_staff(
        db,
        visitor.id,
        visitor.project_id,
        target_staff_id=staff[0].id,
        session_id=session.id,
        expected_queue_entry_id=entry.id,
        send_notification=False,
    )
    assert not result.success
    assert entry.status == "waiting"
    assert session.staff_id is None
    assert db.query(VisitorAssignmentHistory).count() == 0


async def test_existing_session_does_not_consume_another_seat(reception):
    db, visitor, session, _entry, staff, _rule = reception
    session.staff_id = staff[0].id
    visitor.service_status = "active"
    db.commit()
    result = await transfer_service.transfer_to_staff(
        db,
        visitor.id,
        visitor.project_id,
        target_staff_id=staff[0].id,
        session_id=session.id,
        send_notification=False,
    )
    assert result.success, result.message
    assert session.staff_id == staff[0].id


async def test_unlimited_capacity_keeps_an_available_target(reception):
    db, visitor, session, entry, staff, rule = reception
    rule.max_concurrent_chats = None
    for _ in range(11):
        occupy(db, visitor, staff[0])
    db.commit()
    result = await transfer_service.transfer_to_staff(
        db,
        visitor.id,
        visitor.project_id,
        target_staff_id=staff[0].id,
        session_id=session.id,
        expected_queue_entry_id=entry.id,
        send_notification=False,
    )
    assert result.success, result.message
    assert session.staff_id == staff[0].id
