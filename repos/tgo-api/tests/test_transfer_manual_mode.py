"""Seat transfers hand the conversation to a person regardless of prior AI mode."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models import AssignmentSource, Staff, VisitorAssignmentHistory, VisitorAssignmentRule
from app.services import transfer_service
from tests.test_queue_assignment import queue_db, seed_queue  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize("platform_type", ["wechat_local", "wecom", "website"])
@pytest.mark.parametrize("old_mode", [None, "auto", "assist", "manual"])
@pytest.mark.parametrize("auto_commit", [True, False])
async def test_transfer_enters_manual_and_preserves_transaction(
    queue_db, monkeypatch, platform_type, old_mode, auto_commit  # noqa: F811
):
    for model in (Staff, VisitorAssignmentRule, VisitorAssignmentHistory):
        model.__table__.create(queue_db.get_bind())
    visitor, session, _ = seed_queue(
        queue_db, platform_type=platform_type,
        expired_at=datetime.utcnow() + timedelta(minutes=5),
    )
    visitor.service_mode = old_mode
    visitor.ai_disabled = old_mode in {"assist", "manual"}
    visitor.humanization_skill_enabled = True
    staff = Staff(
        id=uuid4(), project_id=visitor.project_id,
        username=uuid4().hex, password_hash="disabled-fixture-login",
    )
    queue_db.add(staff)
    queue_db.commit()
    monkeypatch.setattr(transfer_service, "_add_staff_to_channel", AsyncMock())
    notify = AsyncMock()
    monkeypatch.setattr(transfer_service, "notify_visitor_profile_updated", notify, raising=False)
    result = await transfer_service.transfer_to_staff(
        queue_db, visitor.id, visitor.project_id, source=AssignmentSource.TRANSFER,
        target_staff_id=staff.id, session_id=session.id,
        auto_commit=auto_commit, send_notification=False,
    )
    assert result.success, result.message
    assert visitor.service_mode == "manual"
    assert visitor.ai_disabled is True
    assert visitor.humanization_skill_enabled is False
    assert session.staff_id == staff.id
    if auto_commit:
        notify.assert_awaited_once_with(queue_db, visitor)
    else:
        notify.assert_not_awaited()
        queue_db.rollback()
        assert visitor.service_mode == old_mode
        assert visitor.ai_disabled == (old_mode in {"assist", "manual"})
