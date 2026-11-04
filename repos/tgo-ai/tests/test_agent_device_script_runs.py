"""Approved business runs retain an identity before any device dispatch."""

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import inspect

from app.exceptions import ConflictError, NotFoundError
from app.models.agent_device_script_run import AgentDeviceScriptRun
from app.schemas.agent_device_script_requests import ScriptTrialControl
from app.schemas.agent_device_script_reviews import ScriptReviewWrite
from app.schemas.agent_device_script_runs import ScriptRunStart
from app.services.agent_device_script_reviews import (
    AgentDeviceScriptReviews,
)
from app.services.agent_device_script_runs import AgentDeviceScriptRuns
from tests.test_agent_device_script_binding import (  # noqa: F401
    database as binding_database,
)
from tests.test_agent_device_script_cases import (  # noqa: F401
    PRIVATE,
    command,
    evidence_key,
    service,
)
from tests.test_agent_device_script_trials import trial_db  # noqa: F401


@pytest_asyncio.fixture
async def approved_run(trial_db):  # noqa: F811
    (
        sessions,
        project,
        actor,
        agent,
        _,
        owner,
        binding,
        _,
        engine,
    ) = trial_db
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    def migrate(connection, filename, action):
        path = (
            Path(__file__).parents[1] / "migrations/versions" / filename
        )
        spec = importlib.util.spec_from_file_location(
            "owned_run_migration", path
        )
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with Operations.context(MigrationContext.configure(connection)):
            getattr(migration, action)()

    async with engine.begin() as connection:
        await connection.run_sync(
            migrate,
            "l0a1b2c3d4e5_script_reviews.py",
            "upgrade",
        )
        for action in ("upgrade", "downgrade", "upgrade"):
            await connection.run_sync(
                migrate,
                "m1b2c3d4e5f6_script_runs.py",
                action,
            )
        for action in ("upgrade", "downgrade", "upgrade"):
            await connection.run_sync(
                migrate,
                "n2c3d4e5f6a7_script_run_sources.py",
                action,
            )
        columns = await connection.run_sync(
            lambda sync: inspect(sync).get_columns(
                AgentDeviceScriptRun.__tablename__,
            )
        )
        assert {col["name"] for col in columns} == set(
            AgentDeviceScriptRun.__table__.columns.keys(),
        )
        assert {col["name"]: col["nullable"] for col in columns} == {
            col.name: col.nullable
            for col in AgentDeviceScriptRun.__table__.columns
        }
        indexes = await connection.run_sync(
            lambda sync: inspect(sync).get_indexes(
                AgentDeviceScriptRun.__tablename__,
            )
        )
        assert any(index["column_names"] == ["agent_execution_id"]
                   for index in indexes)
    async with sessions() as db:
        cases = service(db, owner)
        version = await cases.save(
            project, actor, agent, command(binding)
        )
        trial = await cases.run(
            project,
            actor,
            agent,
            version.id,
            ScriptTrialControl(
                request_id=uuid4(),
                binding_id=binding.id,
                binding_revision=binding.revision,
            ),
        )
        approved = await AgentDeviceScriptReviews(cases).decide(
            project,
            actor,
            agent,
            trial.id,
            ScriptReviewWrite(
                case_version_id=version.id,
                case_digest=version.case_digest,
                result_digest=trial.result_digest,
                decision="approved",
            ),
        )
    start = ScriptRunStart(
        request_id=uuid4(),
        report_id=trial.id,
        review_id=approved.review.id,
        parameters={"label": PRIVATE},
    )
    yield (
        sessions, project, actor, agent, owner, start, approved, migrate,
        engine,
    )


@pytest.mark.asyncio
async def test_unknown_business_run_recovers_without_repeating_dispatch(
    approved_run,
):
    (
        sessions,
        project,
        actor,
        agent,
        owner,
        start,
        approved,
        migrate,
        engine,
    ) = approved_run
    from app.exceptions import ExternalServiceError

    owner.failure = ExternalServiceError(
        "device-control", "lost receipt"
    )
    async with sessions() as db:
        runs = AgentDeviceScriptRuns(service(db, owner))
        unknown = await runs.start(project, actor, agent, start)
        assert (
            unknown.status == "unknown"
            and unknown.result_fields is None
        )
        assert (
            owner.posts == 2
        )  # Original trial plus one business dispatch.
        assert (
            await runs.start(project, actor, agent, start)
        ).id == unknown.id
        assert owner.posts == 2
        with pytest.raises(ConflictError, match="未确认"):
            await runs.start(
                project,
                actor,
                agent,
                start.model_copy(update={"request_id": uuid4()}),
            )
        await db.rollback()
        found = await runs.get(project, actor, agent, start.request_id)
        assert found.status == "succeeded"
        assert found.result_fields == {"ready": True, "label": PRIVATE}
        stored = await db.get(AgentDeviceScriptRun, found.id)
        assert PRIVATE not in stored.reference_json
        assert stored.result_digest and stored.dispatch_started_at
        original_signature = stored.reference_digest
        stored.reference_digest = "f" * 64
        await db.commit()
        reads = owner.gets
        with pytest.raises(ConflictError, match="无法核验"):
            await runs.get(project, actor, agent, start.request_id)
        assert owner.gets == reads
        await db.rollback()
        stored = await db.get(AgentDeviceScriptRun, found.id)
        stored.reference_digest = original_signature
        await db.commit()
        with pytest.raises(ConflictError):
            await runs.start(
                project,
                actor,
                agent,
                start.model_copy(
                    update={"parameters": {"label": "changed"}},
                ),
            )
        await db.rollback()
        with pytest.raises(NotFoundError):
            await runs.get(project, uuid4(), agent, start.request_id)
        await AgentDeviceScriptReviews(service(db, owner)).revoke(
            project,
            actor,
            agent,
            start.report_id,
            start.review_id,
        )
        assert (
            await runs.get(
                project,
                actor,
                agent,
                start.request_id,
            )
        ).status == "succeeded"
        assert owner.posts == 2
    with pytest.raises(RuntimeError, match="history exists"):
        async with engine.begin() as connection:
            await connection.run_sync(
                migrate,
                "m1b2c3d4e5f6_script_runs.py",
                "downgrade",
            )


@pytest.mark.asyncio
async def test_cancel_before_dispatch_fences_a_late_original_post(
    approved_run,
):
    sessions, project, actor, agent, owner, start, *_ = approved_run
    async with sessions() as db:
        runs = AgentDeviceScriptRuns(service(db, owner))
        cancelled = await runs.cancel(
            project,
            actor,
            agent,
            start.request_id,
            start.report_id,
            start.review_id,
        )
        assert cancelled.status == "cancelled"
        assert (
            await runs.start(
                project,
                actor,
                agent,
                start,
            )
        ).status == "cancelled"
        assert owner.posts == 1


@pytest.mark.asyncio
async def test_revocation_during_session_start_blocks_the_business_post(
    approved_run,
    monkeypatch,
):
    sessions, project, actor, agent, owner, start, *_ = approved_run

    async def revoke_before_post(identity):
        async with sessions() as db:
            await AgentDeviceScriptReviews(service(db, owner)).revoke(
                project,
                actor,
                agent,
                start.report_id,
                start.review_id,
            )

    monkeypatch.setattr(owner, "start_session", revoke_before_post)
    async with sessions() as db:
        result = await AgentDeviceScriptRuns(service(db, owner)).start(
            project,
            actor,
            agent,
            start,
        )
    assert result.status == "failed" and owner.posts == 1


@pytest.mark.asyncio
async def test_invalid_business_receipt_remains_unknown_until_confirmed(
    approved_run,
):
    import json

    sessions, project, actor, agent, owner, start, *_ = approved_run

    async def forge_returned_identity(call):
        owner.receipts[call.execution_id].session_id = uuid4()

    owner.after_post = forge_returned_identity
    async with sessions() as db:
        runs = AgentDeviceScriptRuns(service(db, owner))
        value = await runs.start(project, actor, agent, start)
        assert value.status == "unknown"
        assert value.error_code == "invalid_receipt"
        assert value.result_fields is None
        again = await runs.get(project, actor, agent, start.request_id)
        assert again.status == "unknown" and again.result_fields is None
        row = await db.get(AgentDeviceScriptRun, value.id)
        original = json.loads(row.reference_json)
        from uuid import UUID

        owner.receipts[row.execution_id].session_id = UUID(
            original["identity"]["session_id"],
        )
        confirmed = await runs.get(
            project, actor, agent, start.request_id
        )
        assert confirmed.status == "succeeded"
        assert confirmed.result_fields["label"] == PRIVATE
        assert owner.posts == 2


@pytest.mark.asyncio
async def test_original_input_cannot_change_while_waiting_for_device_checks(
    approved_run, monkeypatch,
):
    import json

    sessions, project, actor, agent, owner, start, *_ = approved_run
    original_budget = owner.read_script_budget
    dispatched_values = []

    async def mutate_callers_model(*args):
        start.parameters["label"] = "changed-while-preparing"
        return await original_budget(*args)

    async def observe_business_input(call):
        dispatched_values.append(json.loads(call.parameters_json))

    monkeypatch.setattr(owner, "read_script_budget", mutate_callers_model)
    monkeypatch.setattr(owner, "before_post", observe_business_input)
    async with sessions() as db:
        value = await AgentDeviceScriptRuns(service(db, owner)).start(
            project, actor, agent, start,
        )
    assert start.parameters["label"] == "changed-while-preparing"
    assert value.status == "succeeded"
    assert dispatched_values == [{"parameters": {"label": PRIVATE}}]
    assert owner.posts == 2
