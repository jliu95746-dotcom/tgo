"""The actual migration preserves case history and matches both ORM models."""

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import SecretStr
import pytest
from sqlalchemy import inspect

from app.config import settings
from app.models.agent_device_script_case import AgentDeviceScriptCase
from app.models.agent_device_script_evidence import AgentDeviceScriptEvidence
from tests.test_agent_device_script_binding import (  # noqa: F401
    database as binding_database,  # noqa: F401
)
from tests.test_agent_device_script_trials import trial_db  # noqa: F401
from tests.test_agent_device_script_cases import command, service


@pytest.mark.asyncio
async def test_case_migration_matches_schema_and_refuses_history_loss(
    trial_db,  # noqa: F811
    monkeypatch,  # noqa: F811
):
    sessions, project, actor, agent, _, owner, binding, _, engine = trial_db
    monkeypatch.setattr(
        settings,
        "script_trial_evidence_key",
        SecretStr("fixture-migration-key-" + "x" * 32),
    )
    path = Path(__file__).parents[1] / (
        "migrations/versions/j8e9f0a1b2c3_script_case_evidence.py"
    )
    spec = importlib.util.spec_from_file_location("owned_case_shape", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    saves_path = Path(__file__).parents[1] / (
        "migrations/versions/k9f0a1b2c3d4_script_case_save_requests.py"
    )
    saves_spec = importlib.util.spec_from_file_location(
        "owned_save_shape", saves_path
    )
    saves = importlib.util.module_from_spec(saves_spec)
    saves_spec.loader.exec_module(saves)

    def migrate(conn, action):
        with Operations.context(MigrationContext.configure(conn)):
            getattr(migration, action)()

    def run_save_migration(conn, action):
        with Operations.context(MigrationContext.configure(conn)):
            getattr(saves, action)()

    def check(conn):
        inspector = inspect(conn)
        for model in (AgentDeviceScriptCase, AgentDeviceScriptEvidence):
            table = model.__table__
            columns = {
                item["name"]: item
                for item in inspector.get_columns(table.name)
            }
            assert set(columns) == set(table.columns.keys())
            for column in table.columns:
                assert columns[column.name]["nullable"] == column.nullable
                assert columns[column.name]["type"].compile(
                    dialect=conn.dialect
                ) == column.type.compile(dialect=conn.dialect)
            for method, kind in (
                (inspector.get_check_constraints, "CheckConstraint"),
                (inspector.get_unique_constraints, "UniqueConstraint"),
            ):
                assert {item["name"] for item in method(table.name)} == {
                    item.name
                    for item in table.constraints
                    if type(item).__name__ == kind
                }
            foreign = inspector.get_foreign_keys(table.name)
            assert {
                tuple(item["constrained_columns"]) for item in foreign
            } == {
                tuple(column.name for column in constraint.columns)
                for constraint in table.foreign_key_constraints
            }
            assert all(
                not item.get("options", {}).get("ondelete") for item in foreign
            )
            assert {
                tuple(item["column_names"])
                for item in inspector.get_indexes(table.name)
                if not item.get("duplicates_constraint")
            } == {
                tuple(column.name for column in index.columns)
                for index in table.indexes
            }

    async with engine.begin() as db:
        await db.run_sync(check)
        await db.run_sync(lambda conn: run_save_migration(conn, "downgrade"))
        await db.run_sync(migrate, "downgrade")
        await db.run_sync(migrate, "upgrade")
        await db.run_sync(lambda conn: run_save_migration(conn, "upgrade"))
        await db.run_sync(check)
    async with sessions() as db:
        saved = await service(db, owner).save(
            project, actor, agent, command(binding)
        )
    with pytest.raises(RuntimeError, match="history exists"):
        async with engine.begin() as db:
            await db.run_sync(migrate, "downgrade")
    async with sessions() as db:
        assert await db.get(AgentDeviceScriptCase, saved.id) is not None
    assert owner.posts == 0
