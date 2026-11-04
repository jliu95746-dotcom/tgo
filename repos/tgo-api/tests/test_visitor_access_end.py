"""An access end is durable, private, repeatable and cannot revive an old reply."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import get_db
from app.core.exceptions import TGOAPIException, tgo_api_exception_handler, validation_exception_handler
from app.models.ai_interaction_run import AIInteractionRun
from app.models.ai_execution_attempt import AIExecutionAttempt
from app.models.visitor_waiting_queue import VisitorWaitingQueue
from app.models.visitor import Visitor
from app.schemas.visitor_access import VisitorAccessInput
from app.services import ai_interaction_run_service
from app.services import reply_service_mode
from app.services.ai_reply_control import ReplyStopped
from app.services.im_access import authenticate_im_actor, issue_visitor_im_token
from app.services.visitor_access import registration_access, end_access
from tests.test_visitor_access import access_fixture as access_fixture
from tests.test_business_customer_identity import setup as setup


@pytest.fixture
def end_records(access_fixture):
    _, db, visitor, _, _ = access_fixture
    secret, _ = registration_access(db, visitor, VisitorAccessInput(), new=True)
    visitor.platform.api_key = "fixture-only-end-platform"
    run = AIInteractionRun(
        project_id=visitor.project_id, platform_id=visitor.platform_id, visitor_id=visitor.id,
        channel_id=f"{visitor.id}-vtr", channel_type=251, source_message_id="end-source",
        request_fingerprint="a" * 64, response_client_msg_no="end-reply", status="running",
    )
    queue = VisitorWaitingQueue(project_id=visitor.project_id, visitor_id=visitor.id,
        source="visitor", urgency="normal", priority=1, status="waiting")
    db.add_all([run, queue])
    db.commit()
    db.add(AIExecutionAttempt(
        execution_id=uuid4(), project_id=visitor.project_id,
        interaction_run_id=run.id, response_client_msg_no=run.response_client_msg_no,
        status="running",
    ))
    db.commit()
    return db, visitor, secret, run, queue


def test_access_end_cancels_queue_and_late_completion_cannot_reopen_run(end_records, monkeypatch):
    db, visitor, secret, run, queue = end_records
    token = issue_visitor_im_token(visitor, timedelta(hours=1))
    end_access(db, visitor, VisitorAccessInput(visitor_access_token=secret))
    db.refresh(run)
    db.refresh(queue)
    attempt = db.query(AIExecutionAttempt).filter(
        AIExecutionAttempt.interaction_run_id == run.id,
    ).one()
    assert run.status == "failed" and run.error_message == "visitor_access_ended"
    assert attempt.status == "failed" and attempt.completed_at is not None
    assert queue.status == "cancelled" and queue.exited_at
    monkeypatch.setattr(ai_interaction_run_service, "SessionLocal", sessionmaker(bind=db.bind))
    ai_interaction_run_service.mark_ai_interaction_finished(
        run.id, response_client_msg_no=run.response_client_msg_no,
    )
    db.refresh(run)
    assert run.status == "failed" and run.error_message == "visitor_access_ended"
    with pytest.raises(TGOAPIException):
        authenticate_im_actor(db, f"{visitor.id}-vtr", token)


@pytest.mark.asyncio
async def test_public_end_requires_holder_and_repeats_without_secret_echo(end_records, monkeypatch):
    from app.api.v1.endpoints import visitor_access
    from app.services import visitor_access_end

    db, visitor, secret, run, _ = end_records
    kick = AsyncMock()
    ensure = AsyncMock()
    monkeypatch.setattr(visitor_access_end.wukongim_client, "enabled", True)
    monkeypatch.setattr(visitor_access_end.wukongim_client, "kick_user_device", kick)
    monkeypatch.setattr(visitor_access_end.wukongim_client, "register_or_login_user", ensure)
    registry = AsyncMock()
    registry.get.return_value = None
    monkeypatch.setattr(visitor_access_end, "run_registry", registry)
    app = FastAPI()
    app.include_router(visitor_access.router, prefix="/v1/visitors")
    app.dependency_overrides[get_db] = lambda: db
    app.add_exception_handler(TGOAPIException, tgo_api_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    payload = {"platform_api_key": visitor.platform.api_key, "visitor_id": str(visitor.id)}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for invalid in ({}, {"visitor_access_token": "someone-else"}):
            denied = await client.post("/v1/visitors/access/end", json={**payload, **invalid})
            assert denied.status_code == 401
        db.refresh(visitor)
        db.refresh(run)
        assert visitor.access_retired_at is None and run.status == "running"
        kick.assert_not_awaited()
        payload["visitor_access_token"] = secret.get_secret_value()
        first = await client.post("/v1/visitors/access/end", json=payload)
        revision = visitor.access_revision
        repeated = await client.post("/v1/visitors/access/end", json=payload)
        assert first.status_code == repeated.status_code == 200
        assert first.json()["status"] == "ended"
        assert first.json()["upstream_stop_status"] == "pending"
        assert repeated.headers["cache-control"] == "no-store"
        assert visitor.access_revision == revision
        assert secret.get_secret_value() not in first.text
        assert visitor.access_secret_digest not in first.text
        wrong_scope = await client.post("/v1/visitors/access/end", json={**payload, "visitor_id": str(uuid4())})
        assert wrong_scope.status_code == 401
        malformed = await client.post("/v1/visitors/access/end", json={**payload, "visitor_access_token": [secret.get_secret_value()]})
        assert malformed.status_code == 422 and secret.get_secret_value() not in malformed.text
    assert ensure.await_count == kick.await_count == 1
    assert all(call.kwargs == {"uid": f"{visitor.id}-vtr"} for call in ensure.await_args_list)


def test_same_source_cannot_retry_after_visitor_access_ended(end_records):
    db, visitor, secret, run, _ = end_records
    end_access(db, visitor, VisitorAccessInput(visitor_access_token=secret))
    identity = ai_interaction_run_service.AIInteractionIdentity(
        project_id=visitor.project_id, platform_id=visitor.platform_id, visitor_id=visitor.id,
        channel_id=run.channel_id, channel_type=251, source_message_id=run.source_message_id,
    )
    with pytest.raises(TGOAPIException):
        ai_interaction_run_service.claim_ai_interaction(db, identity=identity,
            request_fingerprint=run.request_fingerprint, response_client_msg_no="late-attempt")
    db.refresh(run)
    assert run.status == "failed" and run.response_client_msg_no == "end-reply"


@pytest.mark.asyncio
async def test_final_reply_check_uses_fresh_retired_visitor_even_when_mode_is_auto(end_records, monkeypatch):
    db, visitor, secret, _, _ = end_records
    visitor.service_mode = "auto"
    visitor.ai_disabled = False
    db.commit()
    monkeypatch.setattr(reply_service_mode, "SessionLocal", sessionmaker(bind=db.bind))
    await reply_service_mode.ensure_customer_auto_reply(str(visitor.project_id), str(visitor.id))
    end_access(db, visitor, VisitorAccessInput(visitor_access_token=secret))
    with pytest.raises(ReplyStopped):
        await reply_service_mode.ensure_customer_auto_reply(str(visitor.project_id), str(visitor.id))


@pytest.mark.asyncio
async def test_stale_queue_task_cannot_create_a_new_session_after_end(end_records, monkeypatch):
    from app.services import transfer_service

    db, visitor, secret, _, queue = end_records
    end_access(db, visitor, VisitorAccessInput(visitor_access_token=secret))
    session = AsyncMock(side_effect=AssertionError("Retired access cannot create another session"))
    monkeypatch.setattr(transfer_service, "_get_or_create_session", session)
    result = await transfer_service.transfer_to_staff(db, visitor.id, visitor.project_id)
    assert result.success is False
    session.assert_not_awaited()
    db.refresh(queue)
    assert queue.status == "cancelled"


@pytest.mark.asyncio
async def test_repeat_end_observes_owned_cancel_confirmation_without_restart(end_records, monkeypatch):
    from app.schemas.ai_runs import ReplyRun
    from app.schemas.visitor_access import VisitorAccessEndRequest
    from app.services import visitor_access_end
    from app.services.run_registry import InMemoryRunRegistry

    db, visitor, secret, run, _ = end_records
    registry = InMemoryRunRegistry()
    monkeypatch.setattr(visitor_access_end, "run_registry", registry)
    monkeypatch.setattr(visitor_access_end.wukongim_client, "enabled", False)
    item = ReplyRun(project_id=str(visitor.project_id), channel_id=f"{visitor.id}-vtr",
        channel_type=251, client_msg_no=run.response_client_msg_no)
    await registry.start(item)
    request = VisitorAccessEndRequest(platform_api_key=visitor.platform.api_key,
        visitor_id=visitor.id, visitor_access_token=secret)
    first = await visitor_access_end.end_owned_access(db, request)
    assert first.upstream_stop_status == "pending"
    pending = await registry.get(item.project_id, item.client_msg_no)
    assert pending.status == "cancel_requested" and pending.generation == item.generation
    await registry.finish(item, "cancelled")
    repeated = await visitor_access_end.end_owned_access(db, request)
    assert repeated.upstream_stop_status == "confirmed"
    assert (await registry.get(item.project_id, item.client_msg_no)).generation == item.generation


@pytest.mark.asyncio
async def test_end_checks_every_run_across_pages(end_records, monkeypatch):
    from app.schemas.visitor_access import VisitorAccessEndRequest
    from app.services import visitor_access_end

    db, visitor, secret, _, _ = end_records
    for index in range(130):
        db.add(AIInteractionRun(
            project_id=visitor.project_id, platform_id=visitor.platform_id,
            visitor_id=visitor.id, channel_id=f"{visitor.id}-vtr",
            channel_type=251, source_message_id=f"end-source-{index}",
            request_fingerprint="b" * 64,
            response_client_msg_no=f"end-reply-{index}", status="running",
        ))
    db.commit()
    registry = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(
        channel_id=f"{visitor.id}-vtr", channel_type=251, status="completed",
    )))
    monkeypatch.setattr(visitor_access_end, "run_registry", registry)
    monkeypatch.setattr(visitor_access_end.wukongim_client, "enabled", False)
    request = VisitorAccessEndRequest(
        platform_api_key=visitor.platform.api_key,
        visitor_id=visitor.id, visitor_access_token=secret,
    )

    result = await visitor_access_end.end_owned_access(db, request)

    assert result.upstream_stop_status == "confirmed"
    assert registry.get.await_count == 131


@pytest.mark.asyncio
async def test_end_rechecks_a_later_unknown_run(end_records, monkeypatch):
    from app.schemas.visitor_access import VisitorAccessEndRequest
    from app.services import visitor_access_end

    db, visitor, secret, _, _ = end_records
    for index in range(110):
        db.add(AIInteractionRun(
            project_id=visitor.project_id, platform_id=visitor.platform_id,
            visitor_id=visitor.id, channel_id=f"{visitor.id}-vtr",
            channel_type=251, source_message_id=f"retry-source-{index}",
            request_fingerprint="c" * 64,
            response_client_msg_no=f"retry-reply-{index}", status="running",
        ))
    db.commit()
    missing = {"retry-reply-109"}

    async def read_run(_project_id, client_number):
        if client_number in missing:
            return None
        return SimpleNamespace(
            channel_id=f"{visitor.id}-vtr", channel_type=251,
            status="completed",
        )

    registry = SimpleNamespace(get=AsyncMock(side_effect=read_run))
    monkeypatch.setattr(visitor_access_end, "run_registry", registry)
    monkeypatch.setattr(visitor_access_end.wukongim_client, "enabled", False)
    request = VisitorAccessEndRequest(
        platform_api_key=visitor.platform.api_key,
        visitor_id=visitor.id, visitor_access_token=secret,
    )

    first = await visitor_access_end.end_owned_access(db, request)
    assert first.upstream_stop_status == "pending"
    assert registry.get.await_count == 111
    missing.clear()
    second = await visitor_access_end.end_owned_access(db, request)
    assert second.upstream_stop_status == "confirmed"
    assert registry.get.await_count == 112


@pytest.mark.asyncio
async def test_confirmed_stop_survives_registry_loss(end_records, monkeypatch):
    from app.schemas.visitor_access import VisitorAccessEndRequest
    from app.services import visitor_access_end

    db, visitor, secret, run, _ = end_records
    registry = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(
        channel_id=f"{visitor.id}-vtr", channel_type=251,
        status="completed",
    )))
    monkeypatch.setattr(visitor_access_end, "run_registry", registry)
    monkeypatch.setattr(visitor_access_end.wukongim_client, "enabled", False)
    request = VisitorAccessEndRequest(
        platform_api_key=visitor.platform.api_key,
        visitor_id=visitor.id, visitor_access_token=secret,
    )

    first = await visitor_access_end.end_owned_access(db, request)
    assert first.upstream_stop_status == "confirmed"
    restarted = SimpleNamespace(get=AsyncMock(return_value=None))
    monkeypatch.setattr(visitor_access_end, "run_registry", restarted)
    with Session(db.bind) as restarted_db:
        assert restarted_db.get(AIInteractionRun, run.id).stop_confirmed_at
        repeated = await visitor_access_end.end_owned_access(restarted_db, request)
        assert repeated.upstream_stop_status == "confirmed"
    restarted.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_im_disconnect_receipt_survives_retry(end_records, monkeypatch):
    from app.schemas.visitor_access import VisitorAccessEndRequest
    from app.services import visitor_access_end

    db, visitor, secret, _, _ = end_records
    monkeypatch.setattr(visitor_access_end, "run_registry",
                        SimpleNamespace(get=AsyncMock(return_value=None)))
    monkeypatch.setattr(visitor_access_end.wukongim_client, "enabled", True)
    ensure = AsyncMock()
    kick = AsyncMock()
    monkeypatch.setattr(
        visitor_access_end.wukongim_client, "register_or_login_user", ensure,
    )
    monkeypatch.setattr(visitor_access_end.wukongim_client,
                        "kick_user_device", kick)
    request = VisitorAccessEndRequest(
        platform_api_key=visitor.platform.api_key,
        visitor_id=visitor.id, visitor_access_token=secret,
    )

    first = await visitor_access_end.end_owned_access(db, request)
    assert first.im_disconnect_status == "requested"
    with Session(db.bind) as restarted_db:
        assert restarted_db.get(Visitor, visitor.id).im_disconnect_requested_at
        repeated = await visitor_access_end.end_owned_access(restarted_db, request)
        assert repeated.im_disconnect_status == "requested"
    ensure.assert_awaited_once()
    kick.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_im_disconnect_is_retried_without_false_receipt(
    end_records, monkeypatch,
):
    from app.schemas.visitor_access import VisitorAccessEndRequest
    from app.services import visitor_access_end

    db, visitor, secret, _, _ = end_records
    monkeypatch.setattr(visitor_access_end, "run_registry",
                        SimpleNamespace(get=AsyncMock(return_value=None)))
    monkeypatch.setattr(visitor_access_end.wukongim_client, "enabled", True)
    monkeypatch.setattr(visitor_access_end.wukongim_client,
                        "register_or_login_user", AsyncMock())
    kick = AsyncMock(side_effect=[RuntimeError("IM unavailable"), None])
    monkeypatch.setattr(visitor_access_end.wukongim_client,
                        "kick_user_device", kick)
    request = VisitorAccessEndRequest(
        platform_api_key=visitor.platform.api_key,
        visitor_id=visitor.id, visitor_access_token=secret,
    )

    first = await visitor_access_end.end_owned_access(db, request)
    assert first.im_disconnect_status == "pending"
    db.refresh(visitor)
    assert visitor.im_disconnect_lease_until is None
    with Session(db.bind) as restarted_db:
        assert restarted_db.get(Visitor, visitor.id).im_disconnect_requested_at is None
        repeated = await visitor_access_end.end_owned_access(restarted_db, request)
        assert repeated.im_disconnect_status == "requested"
        assert restarted_db.get(Visitor, visitor.id).im_disconnect_requested_at
    assert kick.await_count == 2


@pytest.mark.asyncio
async def test_concurrent_end_requests_claim_only_one_im_kick(
    end_records, monkeypatch,
):
    from app.services import visitor_access_end

    db, visitor, secret, _, _ = end_records
    end_access(db, visitor, VisitorAccessInput(visitor_access_token=secret))
    monkeypatch.setattr(visitor_access_end, "run_registry",
                        SimpleNamespace(get=AsyncMock(return_value=None)))
    monkeypatch.setattr(visitor_access_end.wukongim_client, "enabled", True)
    monkeypatch.setattr(visitor_access_end.wukongim_client,
                        "register_or_login_user", AsyncMock())
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed_kick(**_kwargs):
        entered.set()
        await release.wait()

    kick = AsyncMock(side_effect=delayed_kick)
    monkeypatch.setattr(visitor_access_end.wukongim_client,
                        "kick_user_device", kick)
    first = asyncio.create_task(
        visitor_access_end.cleanup_retired_access(db, visitor)
    )
    await asyncio.wait_for(entered.wait(), 1)
    try:
        with Session(db.bind) as second_db:
            second_visitor = second_db.get(Visitor, visitor.id)
            second = await asyncio.wait_for(
                visitor_access_end.cleanup_retired_access(
                    second_db, second_visitor,
                ), 0.5,
            )
            assert second.im_disconnect_status == "pending"
        assert kick.await_count == 1
    finally:
        release.set()
    assert (await first).im_disconnect_status == "requested"
    assert kick.await_count == 1


@pytest.mark.asyncio
async def test_expired_im_claim_is_recoverable(end_records, monkeypatch):
    from app.services import visitor_access_end

    db, visitor, secret, _, _ = end_records
    end_access(db, visitor, VisitorAccessInput(visitor_access_token=secret))
    visitor.im_disconnect_lease_until = datetime.now(UTC) - timedelta(seconds=1)
    visitor.im_disconnect_lease_token = uuid4()
    db.commit()
    assert db.scalar(select(Visitor.id).where(
        Visitor.id == visitor.id,
        Visitor.project_id == visitor.project_id,
        Visitor.access_retired_at.is_not(None),
        Visitor.im_disconnect_requested_at.is_(None),
        Visitor.im_disconnect_lease_until <= datetime.now(UTC),
    )) == visitor.id
    monkeypatch.setattr(visitor_access_end, "run_registry",
                        SimpleNamespace(get=AsyncMock(return_value=None)))
    monkeypatch.setattr(visitor_access_end.wukongim_client, "enabled", True)
    monkeypatch.setattr(visitor_access_end.wukongim_client,
                        "register_or_login_user", AsyncMock())
    kick = AsyncMock()
    monkeypatch.setattr(visitor_access_end.wukongim_client,
                        "kick_user_device", kick)

    result = await visitor_access_end.cleanup_retired_access(db, visitor)
    db.refresh(visitor)
    kick.assert_awaited_once()
    assert result.im_disconnect_status == "requested"
    assert visitor.im_disconnect_lease_until is None
    assert visitor.im_disconnect_lease_token is None


@pytest.mark.asyncio
async def test_cancelled_im_disconnect_releases_claim_for_retry(
    end_records, monkeypatch,
):
    from app.services import visitor_access_end

    db, visitor, secret, _, _ = end_records
    end_access(db, visitor, VisitorAccessInput(visitor_access_token=secret))
    monkeypatch.setattr(visitor_access_end, "run_registry",
                        SimpleNamespace(get=AsyncMock(return_value=None)))
    monkeypatch.setattr(visitor_access_end.wukongim_client, "enabled", True)
    monkeypatch.setattr(visitor_access_end.wukongim_client,
                        "register_or_login_user", AsyncMock())
    entered = asyncio.Event()

    async def interrupted_kick(**_kwargs):
        entered.set()
        await asyncio.Event().wait()

    kick = AsyncMock(side_effect=interrupted_kick)
    monkeypatch.setattr(visitor_access_end.wukongim_client,
                        "kick_user_device", kick)
    task = asyncio.create_task(
        visitor_access_end.cleanup_retired_access(db, visitor)
    )
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    db.refresh(visitor)
    assert visitor.im_disconnect_lease_until is None
    assert visitor.im_disconnect_lease_token is None
    assert visitor.im_disconnect_requested_at is None

    kick.side_effect = None
    result = await visitor_access_end.cleanup_retired_access(db, visitor)
    assert result.im_disconnect_status == "requested"
    assert kick.await_count == 2
