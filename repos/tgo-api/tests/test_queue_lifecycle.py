"""Queue expiry cannot close a new/assigned conversation or resurrect old work."""

from datetime import datetime, timedelta
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.models import (
    Platform,
    Project,
    Visitor,
    VisitorSession,
    VisitorWaitingQueue,
)
from app.services import queue_lifecycle


@compiles(JSONB, "sqlite")
def compile_jsonb(_element, _compiler, **_kwargs):
    return "JSON"


@pytest.fixture
def queue_db(monkeypatch):
    # Unit fixtures must never publish to the real database or IM server.
    from app.services import queue_events

    monkeypatch.setattr(queue_events, "publish_queue_updates", AsyncMock())
    engine = create_engine("sqlite://")
    for model in (Project, Platform, Visitor, VisitorSession, VisitorWaitingQueue):
        model.__table__.create(engine)
    with Session(engine, autoflush=False) as db:
        yield db
    engine.dispose()


def seed_queue(db, *, platform_type="website", **changes):
    project = Project(id=uuid4(), name="queue fixture", api_key=uuid4().hex)
    platform = Platform(
        id=uuid4(),
        project_id=project.id,
        type=platform_type,
        api_key=uuid4().hex,
        is_active=True,
    )
    visitor = Visitor(
        id=uuid4(),
        project_id=project.id,
        platform_id=platform.id,
        platform_open_id="fixture-only",
        service_status="queued",
        ai_disabled=True,
    )
    session = VisitorSession(
        id=uuid4(),
        visitor_id=visitor.id,
        project_id=project.id,
        platform_id=platform.id,
        status="open",
    )
    entry = VisitorWaitingQueue(
        id=uuid4(),
        project_id=project.id,
        visitor_id=visitor.id,
        session_id=session.id,
        entered_at=datetime.utcnow() - timedelta(minutes=10),
        expired_at=datetime.utcnow() - timedelta(seconds=1),
        status="waiting",
        extra_metadata={"preserved": "yes"},
    )
    for name, value in changes.items():
        setattr(entry, name, value)
    db.add_all([project, platform, visitor, session, entry])
    db.commit()
    return visitor, session, entry


def test_expiry_closes_only_its_waiting_session_and_persists_notice(queue_db):
    visitor, session, entry = seed_queue(queue_db)
    assert queue_lifecycle.expire_entry(queue_db, entry.id) is True
    queue_db.commit()
    assert entry.status == "expired" and session.status == "closed"
    assert visitor.service_status == "closed"
    assert visitor.ai_disabled is True  # Do not silently turn AI on.
    assert entry.extra_metadata["preserved"] == "yes"
    notice = entry.extra_metadata["queue_timeout_notice"]
    assert notice["history_status"] == "pending"
    assert notice["external_status"] == "not_required"
    assert "api_key" not in str(notice)


@pytest.mark.parametrize("status", ["assigned", "cancelled", "expired"])
def test_terminal_entries_are_never_expired_twice(queue_db, status):
    visitor, session, entry = seed_queue(queue_db, status=status)
    assert queue_lifecycle.expire_entry(queue_db, entry.id) is False
    assert session.status == "open" and visitor.service_status == "queued"
    assert "queue_timeout_notice" not in entry.extra_metadata


def test_unexpired_entry_is_untouched(queue_db):
    visitor, session, entry = seed_queue(
        queue_db,
        expired_at=datetime.utcnow() + timedelta(minutes=1),
    )
    assert queue_lifecycle.expire_entry(queue_db, entry.id) is False
    assert visitor.service_status == "queued" and session.status == "open"


def test_assigned_session_and_active_visitor_are_not_closed(queue_db):
    visitor, session, entry = seed_queue(queue_db)
    visitor.service_status = "active"
    session.staff_id = uuid4()
    queue_db.commit()
    assert queue_lifecycle.expire_entry(queue_db, entry.id) is True
    assert session.status == "open" and visitor.service_status == "active"
    assert "queue_timeout_notice" not in entry.extra_metadata


def test_old_queue_entry_cannot_close_a_newer_session(queue_db):
    visitor, session, entry = seed_queue(queue_db)
    newer = VisitorSession(
        id=uuid4(),
        visitor_id=visitor.id,
        project_id=visitor.project_id,
        platform_id=visitor.platform_id,
        created_at=datetime.utcnow() + timedelta(seconds=1),
    )
    queue_db.add(newer)
    queue_db.commit()
    assert queue_lifecycle.expire_entry(queue_db, entry.id) is True
    assert visitor.service_status == "queued" and newer.status == "open"
    assert "queue_timeout_notice" not in entry.extra_metadata


def test_foreign_project_parent_is_not_modified(queue_db):
    visitor, session, entry = seed_queue(queue_db)
    entry.project_id = uuid4()
    queue_db.commit()
    assert queue_lifecycle.expire_entry(queue_db, entry.id) is True
    assert visitor.service_status == "queued" and session.status == "open"
    assert "queue_timeout_notice" not in entry.extra_metadata


def test_queue_claim_rechecks_expiry_and_scope_under_the_visitor_lock(queue_db):
    visitor, session, entry = seed_queue(queue_db)
    assert queue_lifecycle.lock_waiting_entry(queue_db, visitor, entry.id) is None
    entry.expired_at = datetime.utcnow() + timedelta(minutes=1)
    queue_db.commit()
    assert queue_lifecycle.lock_waiting_entry(queue_db, visitor, entry.id) is entry
    entry.status = "expired"
    queue_db.commit()
    assert queue_lifecycle.lock_waiting_entry(queue_db, visitor, entry.id) is None


def test_cancel_closes_current_wait_without_timeout_notice(queue_db):
    visitor, session, entry = seed_queue(queue_db)
    assert queue_lifecycle.cancel_entry(queue_db, entry.id, visitor.project_id)
    queue_db.commit()
    assert entry.status == "cancelled"
    assert visitor.service_status == "closed" and session.status == "closed"
    assert "queue_timeout_notice" not in entry.extra_metadata
    assert not queue_lifecycle.expire_entry(queue_db, entry.id)


def test_foreign_project_cannot_cancel_and_assignment_cannot_resurrect(queue_db):
    visitor, session, entry = seed_queue(queue_db)
    assert not queue_lifecycle.cancel_entry(queue_db, entry.id, uuid4())
    assert entry.status == "waiting"
    assert queue_lifecycle.expire_entry(queue_db, entry.id)
    queue_db.commit()
    queue_lifecycle.assign_waiting_entries(queue_db, visitor, uuid4())
    queue_db.commit()
    assert entry.status == "expired"


def test_assignment_and_queue_transition_rollback_together(queue_db):
    visitor, session, entry = seed_queue(queue_db)
    staff_id = uuid4()
    visitor.service_status = "active"
    session.staff_id = staff_id
    queue_lifecycle.assign_waiting_entries(queue_db, visitor, staff_id)
    assert entry.status == "assigned"
    queue_db.rollback()
    assert visitor.service_status == "queued" and session.staff_id is None
    assert entry.status == "waiting"
