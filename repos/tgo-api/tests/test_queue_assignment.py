"""Actual transfer integration owns the waiting-entry transition."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.compiler import compiles

from app.models import Staff, VisitorAssignmentHistory, VisitorAssignmentRule
from app.services import transfer_service
from tests.test_queue_lifecycle import queue_db, seed_queue  # noqa: F401


@compiles(ARRAY, "sqlite")
def compile_array(_element, _compiler, **_kwargs):
    return "JSON"


@pytest.mark.asyncio
@pytest.mark.parametrize("auto_commit", [True, False])
async def test_real_transfer_assigns_queue_in_own_transaction(
    queue_db, monkeypatch, auto_commit
):
    for model in (Staff, VisitorAssignmentRule, VisitorAssignmentHistory):
        model.__table__.create(queue_db.get_bind())
    visitor, session, entry = seed_queue(
        queue_db, expired_at=datetime.utcnow() + timedelta(minutes=2)
    )
    staff = Staff(
        id=uuid4(),
        project_id=visitor.project_id,
        username=uuid4().hex,
        password_hash="disabled-fixture-login",
    )
    queue_db.add(staff)
    queue_db.commit()
    monkeypatch.setattr(transfer_service, "_add_staff_to_channel", AsyncMock())
    result = await transfer_service.transfer_to_staff(
        queue_db,
        visitor.id,
        visitor.project_id,
        target_staff_id=staff.id,
        session_id=session.id,
        expected_queue_entry_id=entry.id,
        auto_commit=auto_commit,
    )
    assert result.success, result.message
    assert entry.status == "assigned" and entry.assigned_staff_id == staff.id
    assert entry.last_attempt_at is not None
    assert visitor.service_status == "active" and session.staff_id == staff.id
    if not auto_commit:
        queue_db.rollback()
        assert entry.status == "waiting"
        assert visitor.service_status == "queued" and session.staff_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["waiting", "expired", "cancelled", "assigned"])
async def test_stale_queue_request_stops_before_assignment(
    queue_db, monkeypatch, status
):
    visitor, session, entry = seed_queue(queue_db, status=status)
    assign = AsyncMock()
    monkeypatch.setattr(transfer_service, "assign_staff", assign)
    result = await transfer_service.transfer_to_staff(
        queue_db,
        visitor.id,
        visitor.project_id,
        target_staff_id=uuid4(),
        expected_queue_entry_id=entry.id,
    )
    assert not result.success
    assert "no longer waiting" in result.message
    assign.assert_not_awaited()
    assert entry.status == status


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["active", "closed_session", "new_session"])
async def test_stale_wait_cannot_assign_a_changed_conversation(
    queue_db, monkeypatch, changed
):
    from app.models import VisitorSession

    visitor, session, entry = seed_queue(
        queue_db, expired_at=datetime.utcnow() + timedelta(minutes=2)
    )
    if changed == "active":
        visitor.service_status = "active"
    elif changed == "closed_session":
        session.status = "closed"
    else:
        queue_db.add(VisitorSession(
            id=uuid4(), visitor_id=visitor.id, project_id=visitor.project_id,
            platform_id=visitor.platform_id,
            created_at=datetime.utcnow() + timedelta(seconds=1),
        ))
    queue_db.commit()
    assign = AsyncMock()
    monkeypatch.setattr(transfer_service, "assign_staff", assign)
    result = await transfer_service.transfer_to_staff(
        queue_db, visitor.id, visitor.project_id,
        target_staff_id=uuid4(), expected_queue_entry_id=entry.id,
    )
    assert not result.success
    assign.assert_not_awaited()
