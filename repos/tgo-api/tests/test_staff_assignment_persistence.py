"""Malformed model reasoning must not roll back the human handoff."""

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.models import (
    AIProvider,
    Staff,
    VisitorAssignmentHistory,
    VisitorAssignmentRule,
)
from app.services import transfer_service
from app.services.ai_client import ai_client
from tests import test_queue_assignment as queue_checks

queue_db = queue_checks.queue_db
seed_queue = queue_checks.seed_queue


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", [{"nested": "object"}, ["array"], False])
async def test_invalid_reason_does_not_lose_waiting_customer(
    queue_db, monkeypatch, reason
):
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", False)
    for model in (
        Staff,
        AIProvider,
        VisitorAssignmentRule,
        VisitorAssignmentHistory,
    ):
        model.__table__.create(queue_db.get_bind())
    visitor, session, entry = seed_queue(
        queue_db, expired_at=datetime.utcnow() + timedelta(minutes=5)
    )
    staff = [
        Staff(
            id=uuid4(),
            project_id=visitor.project_id,
            username=uuid4().hex,
            password_hash="disabled-fixture-password",
            role="user",
            status="online",
        )
        for _ in range(2)
    ]
    provider = AIProvider(
        id=uuid4(),
        project_id=visitor.project_id,
        provider="openai",
        name="Synthetic dispatcher",
        api_key="synthetic-unused-key",
        default_model="fixture-model",
        is_active=True,
    )
    rule = VisitorAssignmentRule(
        id=uuid4(),
        project_id=visitor.project_id,
        ai_provider_id=provider.id,
        llm_assignment_enabled=True,
        model="fixture-model",
    )
    queue_db.add_all([*staff, provider, rule])
    queue_db.commit()
    complete = AsyncMock(
        return_value={
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "selected_staff_id": str(staff[0].id),
                                "reasoning": reason,
                            }
                        )
                    }
                }
            ]
        }
    )
    monkeypatch.setattr(ai_client, "chat_completions", complete)
    monkeypatch.setattr(transfer_service, "_add_staff_to_channel", AsyncMock(return_value=None))
    monkeypatch.setattr(
        transfer_service, "notify_visitor_profile_updated", AsyncMock()
    )
    result = await transfer_service.transfer_to_staff(
        queue_db,
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
    complete.assert_awaited_once()
    history = queue_db.query(VisitorAssignmentHistory).one()
    assert isinstance(history.reasoning, str)
    assert "load balancing" in history.reasoning.lower()
    assert history.assigned_staff_id in {member.id for member in staff}
    assert entry.status == "assigned" and visitor.service_status == "active"
    assert session.staff_id == history.assigned_staff_id
    assert visitor.service_mode == "manual" and visitor.ai_disabled is True


@pytest.mark.asyncio
async def test_transfer_error_hides_database_details(
    queue_db, monkeypatch, caplog
):
    from app.models import AssignmentChannelDelivery, ChannelMember
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", False)
    for model in (Staff, VisitorAssignmentRule, VisitorAssignmentHistory):
        model.__table__.create(queue_db.get_bind())
    for model in (ChannelMember, AssignmentChannelDelivery):
        model.__table__.create(queue_db.get_bind())
    visitor, session, _entry = seed_queue(queue_db)
    staff = Staff(
        id=uuid4(),
        project_id=visitor.project_id,
        username=uuid4().hex,
        password_hash="disabled-fixture-password",
        role="user",
    )
    queue_db.add(staff)
    queue_db.commit()
    private = "synthetic-private-dispatcher-database-error"
    attempts = []

    def unavailable():
        attempts.append(True)
        raise SQLAlchemyError(private)

    monkeypatch.setattr(queue_db, "commit", unavailable)
    with caplog.at_level("INFO"):
        result = await transfer_service.transfer_to_staff(
            queue_db,
            visitor.id,
            visitor.project_id,
            target_staff_id=staff.id,
            session_id=session.id,
            send_notification=False,
        )
    assert result.success is False
    assert attempts == [True]
    assert private not in result.message
    assert private not in repr([vars(record) for record in caplog.records])
