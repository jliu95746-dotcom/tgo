"""Model defaults and provider lifecycle use fresh PostgreSQL state."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import (
    AIProvider,
    Project,
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
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not postgres_checks.TEST_URL or not postgres_checks.TEST_SERVER,
        reason="Disposable PostgreSQL and controlled AI HTTP service required",
    ),
]


@pytest.mark.parametrize(
    "case",
    [
        "default",
        "explicit",
        "explicit_without_default",
        "empty_explicit",
        "missing_model",
        "blank_default",
        "disabled",
        "deleted",
        "foreign",
        "hard_deleted_cached",
        "default_changed_cached",
    ],
)
async def test_model_configuration_controls_outbound_request_and_history(
    identity, monkeypatch, case
):
    db, project, staff, platform = identity
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", False)
    monkeypatch.setattr(transfer_service, "_add_staff_to_channel", AsyncMock())
    monkeypatch.setattr(
        transfer_service, "notify_visitor_profile_updated", AsyncMock()
    )
    monkeypatch.setattr(queue_events, "publish_queue_updates", AsyncMock())
    actual_complete = ai_client.chat_completions
    complete = AsyncMock(wraps=actual_complete)
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
        model=None,
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
    if case in {
        "explicit",
        "explicit_without_default",
        "disabled",
        "deleted",
        "foreign",
        "hard_deleted_cached",
    }:
        rule.model = "fixture-model"
    if case == "explicit":
        provider.default_model = "unused-other-model"
    elif case in {"explicit_without_default", "missing_model"}:
        provider.default_model = None
    elif case == "empty_explicit":
        rule.model = ""
    elif case == "blank_default":
        provider.default_model = "  "
    elif case == "disabled":
        provider.is_active = False
    elif case == "deleted":
        provider.deleted_at = datetime.utcnow()
    elif case == "foreign":
        other = Project(name="other-dispatch", api_key="test-" + uuid4().hex)
        db.add(other)
        db.flush()
        provider.project_id = other.id
    elif case == "default_changed_cached":
        provider.default_model = "previous-default"
    db.commit()
    provider_id = provider.id
    if case == "hard_deleted_cached":
        assert rule.ai_provider_id == provider_id
        with Session(db.get_bind()) as elsewhere:
            elsewhere.execute(
                delete(AIProvider).where(AIProvider.id == provider_id)
            )
            elsewhere.commit()
        # A separate committed deletion leaves this session's rule cached.
        assert rule.ai_provider_id == provider_id
    elif case == "default_changed_cached":
        assert provider.default_model == "previous-default"
        with Session(db.get_bind()) as elsewhere:
            elsewhere.execute(
                update(AIProvider)
                .where(AIProvider.id == provider_id)
                .values(default_model="fixture-model")
            )
            elsewhere.commit()
        assert provider.default_model == "previous-default"
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
        visitor_message="SCENARIO:valid",
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
    unavailable = case in {
        "missing_model",
        "blank_default",
        "disabled",
        "deleted",
        "foreign",
        "hard_deleted_cached",
    }
    if unavailable:
        complete.assert_not_awaited()
        assert history.model_used is None and history.prompt_used is None
        assert history.llm_response is None
        assert "load balancing" in history.reasoning.lower()
    else:
        complete.assert_awaited_once()
        assert complete.await_args.kwargs["model"] == "fixture-model"
        assert complete.await_args.kwargs["provider_id"] == str(provider_id)
        assert history.model_used == "fixture-model"
        assert history.reasoning == "synthetic-private-dispatcher-content"
    assert history.assigned_staff_id in {staff.id, colleague.id}
    assert entry.status == "assigned" and visitor.service_status == "active"
    assert session.staff_id == history.assigned_staff_id
    assert visitor.service_mode == "manual" and visitor.ai_disabled is True
