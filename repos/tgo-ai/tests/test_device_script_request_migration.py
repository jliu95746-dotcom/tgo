"""An owned PostgreSQL schema preserves predecessor reports on upgrade."""

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import inspect, select

from app.models.agent_device_script_trial import AgentDeviceScriptTrial
from app.models.agent_device_script_trial_request import (
    AgentDeviceScriptTrialRequest,
)
from tests.test_agent_device_script_binding import (  # noqa: F401
    database as binding_database,  # noqa: F401
)
from tests.test_agent_device_script_trials import trial_db  # noqa: F401


@pytest.mark.asyncio
async def test_request_migration_matches_model_and_preserves_history(
    trial_db,  # noqa: F811
):
    (
        sessions, project, actor, employee, device, _, binding, _, engine
    ) = trial_db
    path = Path(__file__).parents[1] / (
        "migrations/versions/i7d8e9f0a1b2_script_trial_requests.py"
    )
    spec = importlib.util.spec_from_file_location(
        "owned_request_upgrade", path
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def run(connection, action):
        with Operations.context(MigrationContext.configure(connection)):
            getattr(migration, action)()

    def check(connection):
        table = AgentDeviceScriptTrialRequest.__table__
        inspector = inspect(connection)
        actual = {
            item["name"]: item
            for item in inspector.get_columns(table.name)
        }
        assert set(actual) == set(table.columns.keys())
        for column in table.columns:
            assert actual[column.name]["nullable"] == column.nullable
            assert actual[column.name]["type"].compile(
                dialect=connection.dialect
            ) == column.type.compile(dialect=connection.dialect)
        for kind, method, attribute in (
            ("CheckConstraint", inspector.get_check_constraints, "name"),
            ("UniqueConstraint", inspector.get_unique_constraints, "name"),
        ):
            assert {item[attribute] for item in method(table.name)} == {
                item.name for item in table.constraints
                if type(item).__name__ == kind
            }
        foreign = inspector.get_foreign_keys(table.name)
        assert {tuple(item["constrained_columns"]) for item in foreign} == {
            ("agent_id",), ("binding_id",), ("report_id",),
        }
        assert all(not item.get("options", {}).get("ondelete")
                   for item in foreign)
        assert {
            tuple(item["column_names"])
            for item in inspector.get_indexes(table.name)
            if not item.get("duplicates_constraint")
        } == {tuple(column.name for column in index.columns)
              for index in table.indexes}

    async with engine.begin() as db:
        await db.run_sync(check)
        await db.run_sync(run, "downgrade")
    # A predecessor database report fixture is not an accepted business proof.
    now = datetime.now(timezone.utc)
    trial_id = uuid4()
    async with sessions() as db:
        db.add(AgentDeviceScriptTrial(
            id=trial_id, project_id=project, actor_id=actor, agent_id=employee,
            binding_id=binding.id, binding_revision=binding.revision,
            request_id=uuid4(), device_id=device, execution_id=uuid4(),
            command_digest="a" * 64, expected_digest="b" * 64,
            result_digest="b" * 64, policy_digest=binding.policy_digest,
            grant_digest=binding.grant_digest, reference_json="{}",
            reference_digest="c" * 64, status="matched", error_code=None,
            remaining_runs=1, created_at=now, dispatch_claimed_at=now,
            dispatch_deadline_at=now + timedelta(seconds=30),
            completed_at=now, expires_at=now + timedelta(days=1),
        ))
        await db.commit()
    async with engine.begin() as db:
        await db.run_sync(run, "upgrade")
        await db.run_sync(check)
    async with sessions() as db:
        original = await db.scalar(select(AgentDeviceScriptTrialRequest))
        assert original.report_id == trial_id and original.status == "ready"
        assert original.stop_status == "finished"
        assert original.command_digest == "a" * 64
    with pytest.raises(RuntimeError, match="history exists"):
        async with engine.begin() as db:
            await db.run_sync(run, "downgrade")
    async with sessions() as db:
        assert await db.get(AgentDeviceScriptTrial, trial_id) is not None
        assert (await db.scalar(select(
            AgentDeviceScriptTrialRequest
        ))).report_id == trial_id
