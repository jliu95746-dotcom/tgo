"""A retired visitor's pending stop can finish after the HTTP request ends."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models.ai_interaction_run import AIInteractionRun
from app.models.visitor import Visitor
from app.schemas.visitor_access import (
    VisitorAccessEndRequest, VisitorAccessInput,
)
from app.services import visitor_access_end
from app.services.visitor_access import end_access, registration_access
from tests.test_business_customer_identity import setup as setup
from tests.test_visitor_access import access_fixture as access_fixture
from tests.test_visitor_access_end import end_records as end_records


@pytest.mark.asyncio
async def test_background_recovery_finishes_a_pending_end(
    end_records, monkeypatch,
):
    from app.tasks.visitor_end_recovery import recover_pending_visitor_ends

    db, visitor, secret, run, _ = end_records
    registry = SimpleNamespace(get=AsyncMock(return_value=None))
    monkeypatch.setattr(visitor_access_end, "run_registry", registry)
    monkeypatch.setattr(visitor_access_end.wukongim_client, "enabled", False)
    request = VisitorAccessEndRequest(
        platform_api_key=visitor.platform.api_key,
        visitor_id=visitor.id, visitor_access_token=secret,
    )
    assert (await visitor_access_end.end_owned_access(
        db, request,
    )).upstream_stop_status == "pending"
    registry.get.return_value = SimpleNamespace(
        channel_id=f"{visitor.id}-vtr", channel_type=251,
        status="completed",
    )

    sweep = await recover_pending_visitor_ends(
        lambda: Session(db.bind), limit=1,
    )
    assert sweep.checked == 1 and sweep.confirmed == 1
    assert sweep.next_cursor == visitor.id
    with Session(db.bind) as restarted_db:
        assert restarted_db.get(AIInteractionRun, run.id).stop_confirmed_at
    registry.get.return_value = None
    second = await recover_pending_visitor_ends(
        lambda: Session(db.bind), after_visitor_id=sweep.next_cursor,
        limit=1,
    )
    assert second.checked == 0


@pytest.mark.asyncio
async def test_recovery_cursor_passes_an_unresolved_visitor(
    end_records, monkeypatch,
):
    from app.tasks.visitor_end_recovery import recover_pending_visitor_ends

    db, first, secret, run, _ = end_records
    second_id = uuid4()
    while second_id <= first.id:
        second_id = uuid4()
    second = Visitor(
        id=second_id, project_id=first.project_id,
        platform_id=first.platform_id, platform_open_id="second-open-id",
    )
    db.add(second)
    db.commit()
    second_secret, _ = registration_access(
        db, second, VisitorAccessInput(), new=True,
    )
    db.add(AIInteractionRun(
        project_id=second.project_id, platform_id=second.platform_id,
        visitor_id=second.id, channel_id=f"{second.id}-vtr",
        channel_type=251, source_message_id="second-end-source",
        request_fingerprint="d" * 64,
        response_client_msg_no="second-end-reply", status="running",
    ))
    db.commit()
    end_access(db, first, VisitorAccessInput(visitor_access_token=secret))
    end_access(db, second, VisitorAccessInput(
        visitor_access_token=second_secret,
    ))
    assert run.stop_confirmed_at is None
    registry = SimpleNamespace(get=AsyncMock(return_value=None))
    monkeypatch.setattr(visitor_access_end, "run_registry", registry)
    monkeypatch.setattr(visitor_access_end.wukongim_client, "enabled", False)

    first_sweep = await recover_pending_visitor_ends(
        lambda: Session(db.bind), limit=1,
    )
    second_sweep = await recover_pending_visitor_ends(
        lambda: Session(db.bind), after_visitor_id=first_sweep.next_cursor,
        limit=1,
    )
    assert first_sweep.next_cursor == first.id
    assert first_sweep.pending == 1
    assert second_sweep.next_cursor == second.id
    assert second_sweep.pending == 1
    assert registry.get.await_count == 2


@pytest.mark.asyncio
async def test_shutdown_cancels_an_inflight_cleanup(end_records, monkeypatch):
    db, visitor, secret, _, _ = end_records
    end_access(db, visitor, VisitorAccessInput(visitor_access_token=secret))
    started = asyncio.Event()

    async def hang_on_registry(_project_id, _client_number):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(
        visitor_access_end, "run_registry",
        SimpleNamespace(get=AsyncMock(side_effect=hang_on_registry)),
    )
    monkeypatch.setattr(visitor_access_end.wukongim_client, "enabled", False)
    cleaning = asyncio.create_task(
        visitor_access_end.cleanup_retired_access(db, visitor)
    )
    await asyncio.wait_for(started.wait(), 1)
    cleaning.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(cleaning, 1)


@pytest.mark.asyncio
async def test_recovery_never_touches_an_active_visitor(
    end_records, monkeypatch,
):
    from app.tasks.visitor_end_recovery import recover_pending_visitor_ends

    db, visitor, _, run, _ = end_records
    run.status = "failed"
    run.error_message = "visitor_access_ended"
    db.commit()
    registry = SimpleNamespace(get=AsyncMock())
    monkeypatch.setattr(visitor_access_end, "run_registry", registry)
    monkeypatch.setattr(visitor_access_end.wukongim_client, "enabled", True)
    kick = AsyncMock()
    monkeypatch.setattr(visitor_access_end.wukongim_client,
                        "kick_user_device", kick)

    sweep = await recover_pending_visitor_ends(
        lambda: Session(db.bind), limit=1,
    )
    assert visitor.access_retired_at is None
    assert sweep.checked == 0
    registry.get.assert_not_awaited()
    kick.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_retries_only_missing_im_receipt(
    end_records, monkeypatch,
):
    from datetime import UTC, datetime

    from app.tasks.visitor_end_recovery import recover_pending_visitor_ends

    db, visitor, secret, run, _ = end_records
    end_access(db, visitor, VisitorAccessInput(visitor_access_token=secret))
    run.stop_confirmed_at = datetime.now(UTC)
    db.commit()
    registry = SimpleNamespace(get=AsyncMock())
    monkeypatch.setattr(visitor_access_end, "run_registry", registry)
    monkeypatch.setattr(visitor_access_end.wukongim_client, "enabled", True)
    monkeypatch.setattr(visitor_access_end.wukongim_client,
                        "register_or_login_user", AsyncMock())
    kick = AsyncMock()
    monkeypatch.setattr(visitor_access_end.wukongim_client,
                        "kick_user_device", kick)

    sweep = await recover_pending_visitor_ends(
        lambda: Session(db.bind), limit=1,
    )
    assert sweep.checked == 1 and sweep.confirmed == 1
    with Session(db.bind) as restarted_db:
        assert restarted_db.get(Visitor, visitor.id).im_disconnect_requested_at
    registry.get.assert_not_awaited()
    kick.assert_awaited_once()


@pytest.mark.asyncio
async def test_recovery_start_stop_respects_switch_and_single_task(
    monkeypatch,
):
    from app.tasks import visitor_end_recovery as task

    started = asyncio.Event()

    async def wait_until_cancelled():
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(task, "_run_periodically", wait_until_cancelled)
    monkeypatch.setattr(task.settings, "VISITOR_END_RECOVERY_ENABLED", False)
    task.start_visitor_end_recovery()
    assert task._task is None

    monkeypatch.setattr(task.settings, "VISITOR_END_RECOVERY_ENABLED", True)
    try:
        task.start_visitor_end_recovery()
        running = task._task
        await asyncio.wait_for(started.wait(), 1)
        task.start_visitor_end_recovery()
        assert task._task is running
    finally:
        await task.stop_visitor_end_recovery()
    assert task._task is None and running.done()
