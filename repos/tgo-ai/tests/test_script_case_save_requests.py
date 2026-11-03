"""Durable save preparation and cancellation never dispatch a device."""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.exceptions import ConflictError, NotFoundError
from app.exceptions import ValidationError as RequestError
from app.models.agent_device_script_case import AgentDeviceScriptCase
from app.models.agent_device_script_case_request import ScriptCaseSaveRequest
from app.schemas.agent_device_script_cases import ScriptCaseSaveControl
from app.services.agent_device_script_case_requests import ScriptCaseRequests
from tests.test_agent_device_script_binding import (  # noqa: F401
    database as binding_database,
)
from tests.test_agent_device_script_trials import trial_db  # noqa: F401
from tests.test_agent_device_script_cases import command, service, PRIVATE
from tests.test_agent_device_script_cases import evidence_key  # noqa: F401


def control(request):
    return ScriptCaseSaveControl.model_validate(
        request.model_dump(include=set(ScriptCaseSaveControl.model_fields))
    )


@pytest.mark.asyncio
async def test_cancel_during_device_validation_fences_late_save(
    trial_db,  # noqa: F811
    monkeypatch,  # noqa: F811
):
    sessions, project, actor, agent, _, owner, binding, _, _ = trial_db
    entered, release = asyncio.Event(), asyncio.Event()
    original = owner.validate_script_configuration

    async def waiting(*args):
        entered.set()
        await release.wait()
        return await original(*args)

    monkeypatch.setattr(owner, "validate_script_configuration", waiting)
    request = command(binding)
    async with sessions() as writer, sessions() as reader:
        saving = asyncio.create_task(
            service(writer, owner).save(project, actor, agent, request)
        )
        try:
            await asyncio.wait_for(entered.wait(), 30)
            requests = ScriptCaseRequests(service(reader, owner))
            pending = await requests.state(
                project, actor, agent, request.request_id
            )
            assert pending.status == "preparing" and pending.case is None
            assert PRIVATE not in pending.model_dump_json()
            with pytest.raises(ConflictError):
                await asyncio.wait_for(
                    service(reader, owner).save(
                        project, actor, agent, request
                    ),
                    10,
                )
            wrong = control(request).model_copy(
                update={"binding_revision": request.binding_revision + 1}
            )
            with pytest.raises(ConflictError):
                await requests.cancel(project, actor, agent, wrong)
            cancelled = await requests.cancel(
                project, actor, agent, control(request)
            )
            assert cancelled.status == "cancelled"
        finally:
            release.set()
            outcomes = await asyncio.gather(saving, return_exceptions=True)
        assert isinstance(outcomes[0], ConflictError)
        assert await reader.scalar(select(AgentDeviceScriptCase.id)) is None
        assert (
            await requests.state(project, actor, agent, request.request_id)
        ).status == "cancelled"
    assert owner.posts == 0


@pytest.mark.asyncio
async def test_cancel_before_post_and_expiry_are_terminal(
    trial_db,  # noqa: F811
):  # noqa: F811
    sessions, project, actor, agent, _, owner, binding, _, _ = trial_db
    async with sessions() as db:
        cases = service(db, owner)
        requests = ScriptCaseRequests(cases)
        late = command(binding)
        stopped = await requests.cancel(project, actor, agent, control(late))
        assert stopped.status == "cancelled"
        for _ in range(2):
            with pytest.raises(ConflictError):
                await cases.save(project, actor, agent, late)
        other = command(binding)
        row, created = await requests.register(
            project, actor, agent, other, "a" * 64
        )
        assert created
        row.deadline_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.commit()
        expired = await requests.state(project, actor, agent, other.request_id)
        assert expired.status == "failed"
        assert expired.error_code == "preparation_expired"
        with pytest.raises(ConflictError):
            await cases.save(project, actor, agent, other)
        for other_project, other_actor, other_agent in (
            (uuid4(), actor, agent),
            (project, uuid4(), agent),
            (project, actor, uuid4()),
        ):
            with pytest.raises(NotFoundError):
                await requests.state(
                    other_project, other_actor, other_agent, late.request_id
                )
        assert await db.scalar(select(AgentDeviceScriptCase.id)) is None
    assert owner.posts == 0


@pytest.mark.asyncio
async def test_saved_receipt_and_failed_validation_are_recoverable(
    trial_db,  # noqa: F811
    monkeypatch,  # noqa: F811
):
    sessions, project, actor, agent, _, owner, binding, _, _ = trial_db
    async with sessions() as db:
        cases = service(db, owner)
        requests = ScriptCaseRequests(cases)
        request = command(binding)
        saved = await cases.save(project, actor, agent, request)
        observed = await requests.state(
            project, actor, agent, request.request_id
        )
        assert observed.status == "saved" and observed.case.id == saved.id
        cancelled = await requests.cancel(
            project, actor, agent, control(request)
        )
        assert cancelled.status == "saved"  # Cancellation is not undo.
        assert (
            await cases.save(project, actor, agent, request)
        ).id == saved.id
        assert PRIVATE not in observed.model_dump_json()
        bad = command(binding, parameters={"unreviewed": PRIVATE})
        with pytest.raises(RequestError):
            await cases.save(project, actor, agent, bad)
        failed = await requests.state(project, actor, agent, bad.request_id)
        assert failed.status == "failed"
        assert failed.error_code == "preparation_failed"
        assert PRIVATE not in failed.model_dump_json()
        # Legacy committed cases recover without inventing preparation history.
        row = await db.scalar(
            select(ScriptCaseSaveRequest).where(
                ScriptCaseSaveRequest.request_id == request.request_id
            )
        )
        await db.delete(row)
        await db.commit()
        legacy = await requests.state(
            project, actor, agent, request.request_id
        )
        assert legacy.status == "saved" and legacy.case.id == saved.id
    assert owner.posts == 0
