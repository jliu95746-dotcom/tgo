"""A runtime source is sealed separately from the approving administrator."""

from uuid import uuid4

import pytest

from app.exceptions import ConflictError
from app.models.agent_device_script_run import AgentDeviceScriptRun
from app.schemas.agent_device_script_source import ScriptRunSource
from app.schemas.agent_execution import AgentExecutionReference
from app.schemas.knowledge import KnowledgeChannel
from app.services.agent_device_script_runs import AgentDeviceScriptRuns
from tests.test_agent_device_script_binding import (  # noqa: F401
    database as binding_database,
)
from tests.test_agent_device_script_cases import (  # noqa: F401
    PRIVATE, evidence_key, service,
)
from tests.test_agent_device_script_runs import approved_run  # noqa: F401
from tests.test_agent_device_script_trials import trial_db  # noqa: F401


def source_for(project, agent):
    return ScriptRunSource(
        agent_execution_id=uuid4(),
        reference=AgentExecutionReference(
            project_id=project,
            agent_id=agent,
            channel=KnowledgeChannel.INTERNAL,
            configuration_fingerprint="a" * 64,
            policy_revision="b" * 64,
        ),
        workflow_execution_id=uuid4(),
    )


@pytest.mark.asyncio
async def test_source_cannot_be_used_without_a_trusted_runtime_guard(
    approved_run,  # noqa: F811
):
    sessions, project, actor, agent, owner, start, *_ = approved_run
    async with sessions() as db:
        runs = AgentDeviceScriptRuns(service(db, owner))
        with pytest.raises(ConflictError, match="运行来源"):
            await runs.start(
                project, actor, agent, start,
                source=source_for(project, agent),
            )
        assert owner.posts == 1
        checked = []

        async def guard(actual):
            checked.append(actual)

        trusted = AgentDeviceScriptRuns(service(db, owner), source_guard=guard)
        source = source_for(project, agent)
        wrong_project = source.model_copy(update={
            "reference": source.reference.model_copy(
                update={"project_id": uuid4()},
            ),
        })
        with pytest.raises(ConflictError, match="运行来源"):
            await trusted.start(
                project, actor, agent, start, source=wrong_project,
            )
        assert not checked and owner.posts == 1


@pytest.mark.asyncio
async def test_manual_receipt_survives_source_migration_down_and_up(
    approved_run,  # noqa: F811
):
    (
        sessions, project, actor, agent, owner, start, _, migrate, engine,
    ) = approved_run
    async with sessions() as db:
        runs = AgentDeviceScriptRuns(service(db, owner))
        value = await runs.start(project, actor, agent, start)
        row = await db.get(AgentDeviceScriptRun, value.id)
        original_signature = row.reference_digest
        assert row.source_json is None and value.status == "succeeded"
    async with engine.begin() as connection:
        for action in ("downgrade", "upgrade"):
            await connection.run_sync(
                migrate, "n2c3d4e5f6a7_script_run_sources.py", action,
            )
    async with sessions() as db:
        runs = AgentDeviceScriptRuns(service(db, owner))
        found = await runs.get(project, actor, agent, start.request_id)
        row = await db.get(AgentDeviceScriptRun, found.id)
        assert row.reference_digest == original_signature
        assert row.source_json is None and found.status == "succeeded"
        assert found.result_fields["label"] == PRIVATE
        assert owner.posts == 2


@pytest.mark.asyncio
async def test_source_survives_recovery_and_cannot_be_replaced_by_a_manual_run(
    approved_run,  # noqa: F811
):
    sessions, project, actor, agent, owner, start, *rest = approved_run
    source = source_for(project, agent)
    checked = []

    async def guard(actual):
        assert actual == source
        checked.append(actual)

    from app.exceptions import ExternalServiceError
    owner.failure = ExternalServiceError("device-control", "lost receipt")
    async with sessions() as db:
        runs = AgentDeviceScriptRuns(
            service(db, owner), source_guard=guard,
        )
        value = await runs.start(
            project, actor, agent, start, source=source,
        )
        assert value.status == "unknown" and len(checked) >= 3
        row = await db.get(AgentDeviceScriptRun, value.id)
        assert row.agent_execution_id == source.agent_execution_id
        assert ScriptRunSource.model_validate_json(row.source_json) == source
        assert PRIVATE not in row.source_json
        ref = runs._reference(row)
        assert (
            ref.identity.workflow_execution_id == source.workflow_execution_id
        )
        with pytest.raises(ConflictError, match="来源"):
            await runs.start(project, actor, agent, start)
        await db.rollback()
        found = await runs.get(project, actor, agent, start.request_id)
        assert found.status == "succeeded"
        assert found.result_fields["label"] == PRIVATE
        from sqlalchemy import update
        from sqlalchemy.exc import IntegrityError

        async with sessions() as writer:
            with pytest.raises(IntegrityError):
                await writer.execute(update(AgentDeviceScriptRun).where(
                    AgentDeviceScriptRun.id == found.id,
                ).values(source_digest=None))
            await writer.rollback()
        original_source = row.source_json
        row.source_json = source.model_copy(update={
            "agent_execution_id": uuid4(),
        }).model_dump_json()
        await db.commit()
        reads = owner.gets
        with pytest.raises(ConflictError, match="来源"):
            await runs.get(project, actor, agent, start.request_id)
        assert reads == owner.gets and owner.posts == 2
        await db.rollback()
        row = await db.get(AgentDeviceScriptRun, value.id)
        row.source_json = original_source
        await db.commit()
    _, migrate, engine = rest
    with pytest.raises(RuntimeError, match="source history exists"):
        async with engine.begin() as connection:
            await connection.run_sync(
                migrate,
                "n2c3d4e5f6a7_script_run_sources.py",
                "downgrade",
            )


@pytest.mark.asyncio
async def test_source_revoked_while_starting_a_device_session_never_dispatches(
    approved_run, monkeypatch,  # noqa: F811
):
    sessions, project, actor, agent, owner, start, *_ = approved_run
    source = source_for(project, agent)
    allowed = True

    async def guard(actual):
        if not allowed:
            raise ConflictError("员工运行来源已失效")

    async def revoke(identity):
        nonlocal allowed
        allowed = False

    monkeypatch.setattr(owner, "start_session", revoke)
    async with sessions() as db:
        runs = AgentDeviceScriptRuns(service(db, owner), source_guard=guard)
        with pytest.raises(ConflictError, match="运行来源"):
            await runs.start(project, actor, agent, start, source=source)
        row = await runs._row(project, actor, agent, start.request_id)
        assert row.status == "failed"
        assert row.dispatch_started_at is None
        assert owner.posts == 1
