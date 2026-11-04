"""Preparation and physical-stop evidence are separate durable outcomes."""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.exceptions import ConflictError, ExternalServiceError, NotFoundError
from app.models.agent_device_script_trial_request import (
    AgentDeviceScriptTrialRequest,
)
from app.schemas.agent_device_script_requests import ScriptTrialControl
from app.services.agent_device_script_requests import ScriptTrialRequests
from app.services.agent_device_script_trials import (
    AgentDeviceScriptTrialService,
)
from tests.test_agent_device_script_binding import (  # noqa: F401
    database as binding_database,  # noqa: F401
)
from tests.test_agent_device_script_trials import (  # noqa: F401
    trial_db, command,
)


def control(request):
    return ScriptTrialControl(
        **request.model_dump(include=set(ScriptTrialControl.model_fields))
    )


@pytest.mark.asyncio
async def test_preparation_is_durable_and_cancel_fences_late_install(
    trial_db,  # noqa: F811
):
    sessions, project, actor, employee, _, owner, binding, _, _ = trial_db
    entered, release = asyncio.Event(), asyncio.Event()
    original = owner.validate_script_configuration

    async def held(*args):
        entered.set()
        await release.wait()
        return await original(*args)

    owner.validate_script_configuration = held
    request = command(binding)

    async def run():
        async with sessions() as db:
            return await AgentDeviceScriptTrialService(db, owner, owner).start(
                project, actor, employee, request
            )

    task = asyncio.create_task(run())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        async with sessions() as db:
            trials = AgentDeviceScriptTrialService(db, owner, owner)
            pending = await ScriptTrialRequests(trials).state(
                project, actor, employee, request.request_id
            )
            assert pending.status == "preparing" and pending.report is None
            cancelled = await ScriptTrialRequests(trials).cancel(
                project, actor, employee, control(request)
            )
            assert cancelled.status == "cancelled"
            assert cancelled.stop_status == "confirmed"
        release.set()
        with pytest.raises(ConflictError):
            await task
        assert owner.posts == 0
        async with sessions() as db:
            row = await db.scalar(select(AgentDeviceScriptTrialRequest))
            assert row.status == "cancelled" and row.report_id is None
            assert "private-customer-fixture" not in repr(row.__dict__)
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancel_before_start_is_a_tombstone_and_never_reuses_key(
    trial_db,  # noqa: F811
):
    sessions, project, actor, employee, _, owner, binding, _, _ = trial_db
    request = command(binding)
    async with sessions() as db:
        trials = AgentDeviceScriptTrialService(db, owner, owner)
        result = await ScriptTrialRequests(trials).cancel(
            project, actor, employee, control(request)
        )
        assert result.status == "cancelled" and result.report is None
        for _ in range(2):
            with pytest.raises(ConflictError):
                await trials.start(project, actor, employee, request)
    assert owner.posts == 0


@pytest.mark.asyncio
async def test_cancel_between_session_start_and_dispatch_is_proven_unsent(
    trial_db,  # noqa: F811
):
    sessions, project, actor, employee, _, owner, binding, _, _ = trial_db
    request = command(binding)

    async def before_claim(_identity):
        async with sessions() as db:
            trials = AgentDeviceScriptTrialService(db, owner, owner)
            state = await ScriptTrialRequests(trials).cancel(
                project, actor, employee, control(request)
            )
            assert state.stop_status == "confirmed"

    owner.start_session = before_claim
    async with sessions() as db:
        result = await AgentDeviceScriptTrialService(db, owner, owner).start(
            project, actor, employee, request
        )
    assert result.status == "failed" and result.error_code == "interrupted"
    assert owner.posts == 0


@pytest.mark.asyncio
async def test_dispatched_stop_stays_pending_until_original_device_receipt(
    trial_db,  # noqa: F811
):
    sessions, project, actor, employee, _, owner, binding, _, _ = trial_db
    entered, release = asyncio.Event(), asyncio.Event()
    request = command(binding)
    stopped = False

    async def hold(_call):
        entered.set()
        await release.wait()

    async def stop(_identity, status):
        nonlocal stopped
        if status == "cancelled":
            stopped = True

    async def cancelled(call):
        if stopped:
            row = owner.receipts[call.execution_id]
            row.status, row.failure = "canceled", "cancelled"
            row.result_available, row.fields = False, {}

    owner.before_post, owner.after_post, owner.finish_session = (
        hold, cancelled, stop
    )

    async def run():
        async with sessions() as db:
            return await AgentDeviceScriptTrialService(db, owner, owner).start(
                project, actor, employee, request
            )

    task = asyncio.create_task(run())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        async with sessions() as db:
            trials = AgentDeviceScriptTrialService(db, owner, owner)
            state = await ScriptTrialRequests(trials).cancel(
                project, actor, employee, control(request)
            )
            assert state.status == "ready"
            assert state.stop_status == "requested"
            assert state.report.status == "unknown" and stopped
        release.set()
        result = await task
        assert result.status == "failed" and result.error_code == "interrupted"
        async with sessions() as db:
            trials = AgentDeviceScriptTrialService(db, owner, owner)
            state = await ScriptTrialRequests(trials).state(
                project, actor, employee, request.request_id
            )
            assert state.stop_status == "confirmed"
            assert state.report.execution_id == result.execution_id
        assert owner.posts == 1
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_expired_preparation_cannot_install_and_other_actor_cannot_read(
    trial_db,  # noqa: F811
):
    sessions, project, actor, employee, _, owner, binding, _, _ = trial_db
    request = command(binding)
    async with sessions() as db:
        trials = AgentDeviceScriptTrialService(db, owner, owner)
        row, fresh = await ScriptTrialRequests(trials).register(
            project, actor, employee, request
        )
        assert fresh
        row.deadline_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.commit()
        state = await ScriptTrialRequests(trials).state(
            project, actor, employee, request.request_id
        )
        assert state.status == "failed"
        assert state.error_code == "preparation_timeout"
        with pytest.raises(ConflictError):
            await trials.start(project, actor, employee, request)
        with pytest.raises(NotFoundError):
            await ScriptTrialRequests(trials).state(
                project, uuid4(), employee, request.request_id
            )
    assert owner.posts == 0


@pytest.mark.asyncio
async def test_stop_failure_does_not_claim_device_has_stopped(
    trial_db,  # noqa: F811
):
    sessions, project, actor, employee, _, owner, binding, _, _ = trial_db
    request = command(binding)
    owner.failure = True
    async with sessions() as db:
        trials = AgentDeviceScriptTrialService(db, owner, owner)
        result = await trials.start(project, actor, employee, request)
        assert result.status == "unknown"

    async def unavailable(*_args):
        raise ExternalServiceError("device-control", "unavailable")

    owner.read_script = unavailable
    owner.finish_session = unavailable
    async with sessions() as db:
        state = await ScriptTrialRequests(
            AgentDeviceScriptTrialService(db, owner, owner)
        ).cancel(project, actor, employee, control(request))
        assert state.stop_status == "requested"
        assert state.report.status == "unknown"
    assert owner.posts == 1
