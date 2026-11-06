"""Real transfer persistence and AI HTTP in a disposable environment."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

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


@pytest.mark.parametrize(
    "scenario",
    [
        "valid",
        "object",
        "array",
        "boolean",
        "reason_nul",
        "reason_surrogate",
        "raw_nul",
        "raw_surrogate",
        "outside",
        "provider",
        "empty",
    ],
)
async def test_actual_transfer_survives_model_output(
    identity, monkeypatch, caplog, scenario
):
    db, project, staff, platform = identity
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", False)
    monkeypatch.setattr(transfer_service, "_add_staff_to_channel", AsyncMock(return_value=None))
    monkeypatch.setattr(
        transfer_service, "notify_visitor_profile_updated", AsyncMock()
    )
    monkeypatch.setattr(queue_events, "publish_queue_updates", AsyncMock())
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
        api_key="synthetic-unused-provider-key",
        default_model="fixture-model",
    )
    visitor = Visitor(
        project_id=project.id,
        platform_id=platform.id,
        platform_open_id=uuid4().hex,
        service_status="queued",
        service_mode="auto",
        ai_disabled=False,
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
        expired_at=datetime.utcnow() + timedelta(minutes=5),
        entered_at=datetime.utcnow(),
        ai_disabled=True,
    )
    db.add(entry)
    db.commit()
    # Uses the real API-to-AI HTTP client. Only its remote peer is a controlled
    # protocol server; no provider, assignment selection or persistence mock.
    with caplog.at_level("INFO"):
        result = await transfer_service.transfer_to_staff(
            db,
            visitor.id,
            project.id,
            session_id=session.id,
            expected_queue_entry_id=entry.id,
            skip_queue_status_check=True,
            add_to_queue_if_no_staff=False,
            send_notification=False,
            ai_disabled=True,
            visitor_message="SCENARIO:" + scenario,
        )
    assert result.success, result.message
    db.expire_all()
    history = (
        db.query(VisitorAssignmentHistory)
        .filter_by(
            visitor_id=visitor.id,
            project_id=project.id,
        )
        .one()
    )
    assert history.assigned_staff_id in {staff.id, colleague.id}
    assert history.model_used == "fixture-model"
    assert (
        history.prompt_used and "SCENARIO:" + scenario in history.prompt_used
    )
    if scenario == "valid":
        assert history.reasoning == PRIVATE
    else:
        assert "load balancing" in history.reasoning.lower()
        assert PRIVATE not in history.reasoning
    if scenario in {"raw_nul", "raw_surrogate", "empty", "provider"}:
        assert history.llm_response is None
    assert entry.status == "assigned" and visitor.service_status == "active"
    assert session.staff_id == history.assigned_staff_id
    assert visitor.service_mode == "manual" and visitor.ai_disabled is True
    assert PRIVATE not in repr([vars(record) for record in caplog.records])
