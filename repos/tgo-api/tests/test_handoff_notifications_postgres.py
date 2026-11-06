"""Real database claims and the existing IM client with a controlled peer."""

import asyncio
import importlib
from datetime import datetime, timedelta, timezone
from threading import Event
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.orm import Session

from app.models import (
    ManualServiceRequest, Staff, Visitor, VisitorSession, VisitorWaitingQueue,
)
from app.services import human_handoff_service
from app.services.wukongim_client import wukongim_client
from app.utils.encoding import build_visitor_channel_id
from tests import test_authentication_boundaries_postgres as postgres_checks

postgres_engine = postgres_checks.postgres_engine
identity = postgres_checks.identity
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not postgres_checks.TEST_URL or not postgres_checks.TEST_SERVER,
        reason="Disposable PostgreSQL and controlled IM HTTP service required",
    ),
]


def notices():
    return importlib.import_module("app.services.handoff_notifications")


@pytest_asyncio.fixture
async def queued(identity, monkeypatch):
    db, project, _staff, platform = identity
    visitor = Visitor(
        project_id=project.id, platform_id=platform.id,
        platform_open_id=uuid4().hex, service_status="queued",
        service_mode="manual", ai_disabled=True,
    )
    db.add(visitor)
    db.flush()
    session = VisitorSession(
        project_id=project.id, platform_id=platform.id,
        visitor_id=visitor.id, status="open",
    )
    db.add(session)
    db.flush()
    entry = VisitorWaitingQueue(
        project_id=project.id, visitor_id=visitor.id, session_id=session.id,
        status="waiting", ai_disabled=True,
        expired_at=datetime.utcnow() + timedelta(minutes=5),
    )
    request = ManualServiceRequest(
        project_id=project.id, visitor_id=visitor.id,
        reason="Synthetic handoff",
        channel_id=build_visitor_channel_id(visitor.id), channel_type=251,
        status="pending", request_metadata={},
    )
    db.add_all([entry, request])
    db.commit()
    receipt = {
        "request_id": str(request.id), "entry_id": str(entry.id),
        "status": "waiting",
    }
    monkeypatch.setattr(wukongim_client, "enabled", True)
    monkeypatch.setattr(
        wukongim_client, "base_url", postgres_checks.TEST_SERVER,
    )
    monkeypatch.setattr(wukongim_client, "_http_client", None)
    monkeypatch.setattr(
        human_handoff_service, "notify_visitor_profile_updated", AsyncMock()
    )
    try:
        yield db, visitor, request, receipt, session, entry
    finally:
        await wukongim_client.aclose()
        db.rollback()
        db.query(ManualServiceRequest).filter_by(
            project_id=project.id,
        ).delete(synchronize_session=False)
        db.commit()


async def finish(queued):
    db, visitor, request, receipt, *_ = queued
    return await human_handoff_service._finish_handoff(
        db=db, request=request, visitor=visitor, receipt=receipt,
        action_digest="synthetic-digest", is_duplicate=False,
        content="已收到人工客服请求。", channel_id=request.channel_id,
        channel_type=251,
    )


async def statistics(request_id):
    async with httpx.AsyncClient(
        base_url=postgres_checks.TEST_SERVER,
    ) as client:
        result = await client.get(f"/fixture-notices/{request_id}")
    result.raise_for_status()
    return result.json()


def age(db, request):
    raw = dict(request.request_metadata[notices().NOTICE_KEY])
    raw["updated_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=1)
    ).isoformat()
    request.request_metadata = {
        **request.request_metadata, notices().NOTICE_KEY: raw,
    }
    db.commit()


def stage(queued):
    db, visitor, request, receipt, *_ = queued
    request.request_metadata = {
        **request.request_metadata, "handoff_receipt": receipt,
    }
    notices().stage_handoff_notice(
        request, visitor, receipt, content="已收到人工客服请求。",
        channel_id=request.channel_id, channel_type=251,
    )
    db.commit()


async def test_committed_notice_is_sent_without_holding_business_locks(queued):
    db, _visitor, request, *_ = queued
    result = await finish(queued)
    assert result["notification_status"] == "submitted"
    stats = await statistics(request.id)
    assert stats["send_calls"] == 1 and stats["unlocked"] is True
    assert request.request_metadata["handoff_notification"]["state"] == "sent"
    assert await notices().deliver_handoff_notice(db, request.id) == "sent"
    assert (await statistics(request.id))["send_calls"] == 1


@pytest.mark.parametrize(
    "scenario", ["accepted_error", "missing", "foreign", "lookup_error"],
)
async def test_uncertain_http_send_is_queried_without_resending(
    queued, scenario,
):
    db, _visitor, request, *_ = queued
    request.request_metadata = {"synthetic_notice_scenario": scenario}
    db.commit()
    assert (await finish(queued))["notification_status"] == "unknown"
    age(db, request)
    outcome = await notices().recover_handoff_notices(db)
    assert outcome.sent == (1 if scenario == "accepted_error" else 0)
    assert request.request_metadata["notification_status"] == (
        "submitted" if scenario == "accepted_error" else "unknown"
    )
    stats = await statistics(request.id)
    assert stats["send_calls"] == 1 and stats["lookup_calls"] == 1


async def test_disabled_im_can_resume_a_never_attempted_notice(
    queued, monkeypatch,
):
    db, _visitor, request, *_ = queued
    monkeypatch.setattr(wukongim_client, "enabled", False)
    assert (await finish(queued))["notification_status"] == "unknown"
    assert request.request_metadata["handoff_notification"]["state"] == (
        "pending"
    )
    monkeypatch.setattr(wukongim_client, "enabled", True)
    assert (await notices().recover_handoff_notices(db)).sent == 1
    assert (await statistics(request.id))["send_calls"] == 1


async def test_database_receipt_failure_is_reconciled_from_http_history(
    queued, monkeypatch,
):
    db, _visitor, request, *_ = queued
    request_id = request.id
    stage(queued)
    original = db.commit
    attempts = 0

    def fail_second():
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise RuntimeError("synthetic receipt commit failure")
        original()

    with monkeypatch.context() as scope:
        scope.setattr(db, "commit", fail_second)
        with pytest.raises(RuntimeError, match="synthetic receipt"):
            await notices().deliver_handoff_notice(db, request_id)
    db.rollback()
    with Session(db.get_bind()) as other:
        saved = other.get(ManualServiceRequest, request_id)
        assert saved.request_metadata["handoff_notification"]["state"] == (
            "sending"
        )
    age(db, request)
    assert (await notices().recover_handoff_notices(db)).sent == 1
    stats = await statistics(request_id)
    assert stats["send_calls"] == stats["lookup_calls"] == 1


async def test_independent_workers_only_send_one_http_notice(
    queued, monkeypatch,
):
    db, _visitor, request, *_ = queued
    stage(queued)
    request_id = request.id
    entered, release = Event(), Event()
    original = human_handoff_service._notify_handoff

    async def pause(**kwargs):
        entered.set()
        assert release.wait(15), "Test sender was not released"
        return await original(**kwargs)

    monkeypatch.setattr(human_handoff_service, "_notify_handoff", pause)
    engine = db.get_bind()

    async def work():
        try:
            with Session(engine) as worker:
                return await notices().deliver_handoff_notice(
                    worker, request_id,
                )
        finally:
            await wukongim_client.aclose()

    first = asyncio.create_task(asyncio.to_thread(lambda: asyncio.run(work())))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        with Session(engine) as other:
            assert await notices().deliver_handoff_notice(
                other, request_id,
            ) == "sending"
    finally:
        release.set()
        result = await first
    assert result == "sent"
    assert (await statistics(request_id))["send_calls"] == 1


async def test_retired_visitor_does_not_trigger_history_lookup(queued):
    db, visitor, request, *_ = queued
    request.request_metadata = {"synthetic_notice_scenario": "accepted_error"}
    db.commit()
    assert (await finish(queued))["notification_status"] == "unknown"
    age(db, request)
    visitor.access_retired_at = datetime.utcnow()
    db.commit()
    assert (await notices().recover_handoff_notices(db)).skipped == 1
    stats = await statistics(request.id)
    assert stats["send_calls"] == 1 and stats["lookup_calls"] == 0


@pytest.mark.parametrize("enabled", [True, False])
async def test_active_handoff_notice_checks_staff_before_http(queued, enabled):
    db, visitor, request, receipt, session, entry = queued
    staff = db.query(Staff).filter_by(project_id=visitor.project_id).one()
    staff.account_enabled = enabled
    session.staff_id = staff.id
    visitor.service_status = "active"
    request.status = "in_progress"
    entry.status = "assigned"
    receipt.update({
        "status": "active", "session_id": str(session.id),
        "assigned_staff_id": str(staff.id),
    })
    db.commit()
    result = await finish(queued)
    assert result["notification_status"] == (
        "submitted" if enabled else "unknown"
    )
    assert request.request_metadata["handoff_notification"]["state"] == (
        "sent" if enabled else "skipped"
    )
    assert (await statistics(request.id))["send_calls"] == int(enabled)
