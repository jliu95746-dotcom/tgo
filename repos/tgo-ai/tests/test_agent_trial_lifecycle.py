"""Cancellation, expiry and request retries share durable terminal state."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.agent_test_report import AgentTestReport
from app.models.base import BaseModel
from app.models.project import Project
from app.runtime.supervisor.infrastructure.services import AIServiceClient
from app.schemas.agent_trials import AgentTrialRequest
from app.services.agent_trials import AgentTrialService
from tests.test_agent_trial_reports import prepared


@pytest.mark.asyncio
async def test_durable_revision_snapshot_and_batched_history_rechecks(trial_database, monkeypatch):
    from app.schemas.agent_trials import TrialKnowledgeRevision, TrialKnowledgeSnapshot
    from app.services import agent_trial_dependencies, agent_trials
    db, project, _ = trial_database
    agent, runtime, service = await prepared(db, project, monkeypatch)
    document = runtime.run_knowledge_trial.return_value.knowledge_evidence.documents[0]
    saved = TrialKnowledgeSnapshot(channel="web", binding_mode="explicit", captured_at=datetime.now(UTC),
        collections=[TrialKnowledgeRevision(collection_id=document.collection_id, resource_revision="a" * 64)])
    lookup = AsyncMock(return_value=saved)
    monkeypatch.setattr(agent_trials, "read_knowledge_snapshot", lookup)
    monkeypatch.setattr(agent_trial_dependencies, "read_knowledge_snapshot", lookup)
    creator = uuid4()
    reports = []
    for _ in range(2):
        reports.append(await service.start(project.id, creator, agent.id,
            AgentTrialRequest(message="材质？", request_key=uuid4())))
    assert all(value.applicability.status == "current" for value in reports)
    assert all(value.knowledge_snapshot.verification == "consistent" for value in reports)
    lookup.return_value = saved.model_copy(update={"collections": [
        saved.collections[0].model_copy(update={"resource_revision": "b" * 64}),
    ]})
    lookup.reset_mock()
    historical = await service.list(project.id, creator, agent.id)
    assert len(historical) == 2 and all(value.applicability.status == "changed" for value in historical)
    lookup.assert_awaited_once()  # One owned resource read for this employee/channel.
    assert all(value.answer == reports[0].answer and value.can_enable is False for value in historical)
    with pytest.raises(HTTPException):
        await service.get(uuid4(), creator, agent.id, reports[0].id)
    assert lookup.await_count == 1


@pytest.mark.asyncio
async def test_knowledge_change_during_generation_is_permanently_recorded(trial_database, monkeypatch):
    from app.schemas.agent_trials import TrialKnowledgeRevision, TrialKnowledgeSnapshot
    from app.services import agent_trials
    db, project, _ = trial_database
    agent, runtime, service = await prepared(db, project, monkeypatch)
    document = runtime.run_knowledge_trial.return_value.knowledge_evidence.documents[0]
    before = TrialKnowledgeSnapshot(channel="web", binding_mode="explicit", captured_at=datetime.now(UTC),
        collections=[TrialKnowledgeRevision(collection_id=document.collection_id, resource_revision="a" * 64)])
    after = before.model_copy(update={"collections": [
        before.collections[0].model_copy(update={"resource_revision": "b" * 64}),
    ]})
    monkeypatch.setattr(agent_trials, "read_knowledge_snapshot", AsyncMock(side_effect=[before, after]))
    value = await service.start(project.id, uuid4(), agent.id, AgentTrialRequest(message="材质？", request_key=uuid4()))
    assert value.status == "completed" and value.knowledge_snapshot.verification == "changed"
    assert value.applicability.status == "changed" and value.can_enable is False


@pytest_asyncio.fixture
async def trial_database(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'trials.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(BaseModel.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        project = Project(id=uuid4(), name="隔离试聊", api_key=f"ak_{uuid4().hex}")
        db.add(project)
        await db.commit()
        yield db, project, sessions
    await engine.dispose()


@pytest.mark.asyncio
async def test_persistent_cancel_stops_actual_model_and_does_not_resurrect(trial_database, monkeypatch):
    db, project, sessions = trial_database
    agent, runtime, service = await prepared(db, project, monkeypatch)
    started, stopped = asyncio.Event(), asyncio.Event()

    async def running(*args):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    runtime.run_knowledge_trial.side_effect = running
    creator, request = uuid4(), AgentTrialRequest(message="材质？", request_key=uuid4())
    task = asyncio.create_task(service.start(project.id, creator, agent.id, request))
    try:
        await asyncio.wait_for(started.wait(), 2)
        async with sessions() as observer:
            other = AgentTrialService(observer, runtime)
            report = await other.by_request(project.id, creator, agent.id, request.request_key)
            cancelled = await other.cancel(project.id, creator, agent.id, report.id)
            assert cancelled.status == "cancelled"
        result = await asyncio.wait_for(task, 2)
        assert stopped.is_set() and result.status == "cancelled"
        assert result.answer == "" and result.sources == []
        assert (await service.start(project.id, creator, agent.id, request)).id == result.id
        runtime.run_knowledge_trial.assert_awaited_once()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_stale_expiry_cannot_replace_a_completed_report(trial_database, monkeypatch):
    db, project, sessions = trial_database
    agent, runtime, service = await prepared(db, project, monkeypatch)
    creator, key = uuid4(), uuid4()
    row = AgentTestReport(
        id=uuid4(), project_id=project.id, created_by=creator, agent_id=agent.id,
        request_key=key, request_digest="d" * 64, status="running", channel="web",
        question="材质？", answer="", sources=[],
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    db.add(row)
    await db.commit()
    async with sessions() as concurrent:
        await concurrent.execute(update(AgentTestReport).where(AgentTestReport.id == row.id).values(
            status="completed", answer="已完成", completed_at=datetime.now(UTC),
        ))
        await concurrent.commit()
    refreshed = await service._expire(row)
    assert refreshed.status == "completed" and refreshed.answer == "已完成"


@pytest.mark.asyncio
async def test_timeout_covers_employee_preparation_and_cleans_up(trial_database, monkeypatch):
    db, project, _ = trial_database
    agent, runtime, service = await prepared(db, project, monkeypatch)
    stopped = asyncio.Event()

    async def slow_load(*args, **kwargs):
        try:
            await asyncio.sleep(20)
        finally:
            stopped.set()

    monkeypatch.setattr(AIServiceClient, "get_trial_agent", slow_load)
    report = await asyncio.wait_for(service.start(
        project.id, uuid4(), agent.id,
        AgentTrialRequest(message="材质？", request_key=uuid4(), timeout=5),
    ), 8)
    assert report.status == "failed" and report.error_code == "timeout"
    assert stopped.is_set()
    runtime.run_knowledge_trial.assert_not_awaited()


@pytest.mark.asyncio
async def test_disconnect_cancellation_is_durable(trial_database, monkeypatch):
    db, project, _ = trial_database
    agent, runtime, service = await prepared(db, project, monkeypatch)
    started, stopped = asyncio.Event(), asyncio.Event()

    async def running(*args):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    runtime.run_knowledge_trial.side_effect = running
    creator, key = uuid4(), uuid4()
    task = asyncio.create_task(service.start(
        project.id, creator, agent.id, AgentTrialRequest(message="材质？", request_key=key),
    ))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    report = await service.by_request(project.id, creator, agent.id, key)
    assert stopped.is_set() and report.status == "cancelled"


@pytest.mark.asyncio
async def test_authorization_is_required_only_for_a_new_execution(trial_database, monkeypatch):
    db, project, _ = trial_database
    agent, runtime, service = await prepared(db, project, monkeypatch)
    creator, request = uuid4(), AgentTrialRequest(message="材质？", request_key=uuid4())
    authorize = AsyncMock()
    first = await service.start(project.id, creator, agent.id, request, authorize=authorize)
    again = await service.start(project.id, creator, agent.id, request, authorize=authorize)
    assert first.id == again.id
    authorize.assert_awaited_once()
    runtime.run_knowledge_trial.assert_awaited_once()


@pytest.mark.asyncio
async def test_denied_quota_never_calls_model_or_echoes_authorization_details(trial_database, monkeypatch):
    db, project, _ = trial_database
    agent, runtime, service = await prepared(db, project, monkeypatch)
    creator, request = uuid4(), AgentTrialRequest(message="材质？", request_key=uuid4())
    authorize = AsyncMock(side_effect=HTTPException(402, "secret-provider-credentials"))
    with pytest.raises(HTTPException) as error:
        await service.start(project.id, creator, agent.id, request, authorize=authorize)
    assert error.value.status_code == 402
    assert "secret-provider-credentials" not in str(error.value.detail)
    report = await service.by_request(project.id, creator, agent.id, request.request_key)
    assert report.status == "blocked" and report.error_code == "authorization_denied"
    runtime.run_knowledge_trial.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_and_request_lookup_are_scoped(trial_database, monkeypatch):
    db, project, _ = trial_database
    agent, runtime, service = await prepared(db, project, monkeypatch)
    creator, request = uuid4(), AgentTrialRequest(message="材质？", request_key=uuid4())
    report = await service.start(project.id, creator, agent.id, request)
    for scope in [(uuid4(), creator, agent.id), (project.id, uuid4(), agent.id), (project.id, creator, uuid4())]:
        with pytest.raises(HTTPException) as error:
            await service.cancel(*scope, report.id)
        assert error.value.status_code == 404
        with pytest.raises(HTTPException):
            await service.by_request(*scope, request.request_key)
    # Cancelling an already completed report preserves its answer and status.
    cancelled = await service.cancel(project.id, creator, agent.id, report.id)
    assert cancelled.model_dump(exclude={"applicability"}) == report.model_dump(exclude={"applicability"})


@pytest.mark.asyncio
async def test_late_completion_cannot_turn_an_expired_lease_into_success(trial_database, monkeypatch):
    db, project, _ = trial_database
    agent, runtime, service = await prepared(db, project, monkeypatch)
    response = runtime.run_knowledge_trial.return_value

    async def late_response(*args):
        await db.execute(update(AgentTestReport).where(
            AgentTestReport.agent_id == agent.id, AgentTestReport.status == "running",
        ).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
        await db.commit()
        return response

    runtime.run_knowledge_trial.side_effect = late_response
    result = await service.start(project.id, uuid4(), agent.id, AgentTrialRequest(
        message="材质？", request_key=uuid4(),
    ))
    assert result.status == "failed" and result.error_code == "interrupted"
    assert result.answer == ""
