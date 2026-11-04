"""Original requests terminate durably before a late private query can dispatch."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.exceptions import ConflictError
from app.models.agent_business_request import AgentBusinessRequest
from app.models.agent_business_trial import AgentBusinessTrial
from app.schemas.agent_business_requests import BusinessRequestControl
from app.services.agent_business_preparation import AgentBusinessPreparation
from app.services.agent_business_trials import AgentBusinessTrialService
from tests.test_agent_business_trials import trial


def control(request):
    return BusinessRequestControl(
        request_id=request.request_id,
        connection_id=request.connection_id,
        operation=request.operation,
    )


@pytest.mark.asyncio
async def test_cancel_before_registration_blocks_the_late_original_request(trial):
    db, trials, owner, fingerprint, project, actor, agent, request = trial
    preparation = AgentBusinessPreparation(trials)
    result = await preparation.cancel(project, actor, agent, control(request))
    assert result.status == "cancelled" and result.report is None
    with pytest.raises(ConflictError):
        await trials.prepare(project, actor, agent, request)
    assert (
        await preparation.state(project, actor, agent, request.request_id)
    ) == result
    assert await db.scalar(select(AgentBusinessTrial.id)) is None
    owner.query_agent_business_trial.assert_not_awaited()


@pytest.mark.asyncio
async def test_registered_preparation_is_visible_and_contains_no_private_inputs(trial):
    db, trials, owner, fingerprint, project, actor, agent, request = trial
    preparation = AgentBusinessPreparation(trials)
    row, created = await preparation.register(project, actor, agent, request)
    assert created
    result = await preparation.state(project, actor, agent, request.request_id)
    assert result.status == "preparing" and result.report is None
    stored = repr({key: getattr(row, key) for key in row.__table__.columns.keys()})
    for secret in (
        request.order_no.get_secret_value(),
        request.customer_session.get_secret_value(),
    ):
        assert secret not in stored and secret not in result.model_dump_json()
    with pytest.raises(ConflictError):
        await trials.prepare(
            project, actor, agent, request.model_copy(update={"request_id": uuid4()})
        )


@pytest.mark.asyncio
async def test_restart_expiration_is_terminal_and_never_replays_inputs(trial):
    db, trials, owner, fingerprint, project, actor, agent, request = trial
    preparation = AgentBusinessPreparation(trials)
    row, _ = await preparation.register(project, actor, agent, request)
    row.deadline_at = datetime.now(UTC) - timedelta(seconds=1)
    await db.commit()
    result = await AgentBusinessPreparation(trials).state(
        project, actor, agent, request.request_id
    )
    assert result.status == "failed" and result.error_code == "preparation_timeout"
    with pytest.raises(ConflictError):
        await trials.prepare(project, actor, agent, request)
    owner.query_agent_business_trial.assert_not_awaited()


@pytest.mark.asyncio
async def test_owner_wait_has_a_deadline_and_saves_a_terminal_receipt(trial):
    db, trials, owner, fingerprint, project, actor, agent, request = trial

    async def wait(_query):
        await asyncio.Event().wait()

    owner.get_agent_business_connection.side_effect = wait
    preparation = AgentBusinessPreparation(trials, preparation_seconds=0.05)
    with pytest.raises(ConflictError):
        await asyncio.wait_for(preparation.prepare(project, actor, agent, request), 2)
    state = await preparation.state(project, actor, agent, request.request_id)
    assert state.status == "failed" and state.error_code == "preparation_timeout"
    assert await db.scalar(select(AgentBusinessTrial.id)) is None


@pytest.mark.asyncio
async def test_cancel_while_owner_is_waiting_prevents_late_report_creation(trial):
    db, trials, owner, fingerprint, project, actor, agent, request = trial
    entered, release = asyncio.Event(), asyncio.Event()
    previous = owner.get_agent_business_connection.side_effect

    async def wait(query):
        entered.set()
        await release.wait()
        return previous(query)

    owner.get_agent_business_connection.side_effect = wait
    task = asyncio.create_task(trials.prepare(project, actor, agent, request))
    await asyncio.wait_for(entered.wait(), 2)
    async with async_sessionmaker(db.bind, expire_on_commit=False)() as other:
        independent = AgentBusinessTrialService(
            other, owner=owner, fingerprint=fingerprint
        )
        cancelled = await AgentBusinessPreparation(independent).cancel(
            project, actor, agent, control(request)
        )
    assert cancelled.status == "cancelled"
    release.set()
    with pytest.raises(ConflictError):
        await task
    assert await db.scalar(select(AgentBusinessTrial.id)) is None
    owner.query_agent_business_trial.assert_not_awaited()


@pytest.mark.asyncio
async def test_original_scope_cannot_be_replaced_by_cancel_metadata(trial):
    db, trials, owner, fingerprint, project, actor, agent, request = trial
    preparation = AgentBusinessPreparation(trials)
    await preparation.register(project, actor, agent, request)
    with pytest.raises(ConflictError):
        await preparation.cancel(
            project,
            actor,
            agent,
            control(request).model_copy(update={"connection_id": uuid4()}),
        )
    with pytest.raises(ConflictError):
        await preparation.register(
            project,
            actor,
            agent,
            request.model_copy(update={"order_no": SecretStr("different-order")}),
        )


@pytest.mark.asyncio
async def test_request_cancel_also_revokes_a_report_without_replaying(trial):
    db, trials, owner, fingerprint, project, actor, agent, request = trial
    report, command = await trials.prepare(project, actor, agent, request)
    preparation = AgentBusinessPreparation(trials)
    state = await preparation.cancel(project, actor, agent, control(request))
    assert state.status == "ready" and state.report.id == report.id
    assert state.report.status == "cancelled"
    await trials.run(command)
    owner.query_agent_business_trial.assert_not_awaited()
    replay, next_command = await trials.prepare(project, actor, agent, request)
    assert replay.id == report.id and next_command is None


@pytest.mark.asyncio
async def test_detached_preparation_persists_interrupted_state(trial):
    db, trials, owner, fingerprint, project, actor, agent, request = trial
    entered = asyncio.Event()

    async def wait(_query):
        entered.set()
        await asyncio.Event().wait()

    owner.get_agent_business_connection.side_effect = wait
    task = asyncio.create_task(trials.prepare(project, actor, agent, request))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    state = await AgentBusinessPreparation(trials).state(
        project, actor, agent, request.request_id
    )
    assert state.status == "failed" and state.error_code == "interrupted"
    assert await db.scalar(select(AgentBusinessTrial.id)) is None


@pytest.mark.asyncio
async def test_duplicate_preparing_request_does_not_start_a_second_owner_check(trial):
    db, trials, owner, fingerprint, project, actor, agent, request = trial
    preparation = AgentBusinessPreparation(trials)
    await preparation.register(project, actor, agent, request)
    with pytest.raises(ConflictError):
        await trials.prepare(project, actor, agent, request)
    owner.get_agent_business_connection.assert_not_awaited()
    assert len((await db.scalars(select(AgentBusinessRequest))).all()) == 1
