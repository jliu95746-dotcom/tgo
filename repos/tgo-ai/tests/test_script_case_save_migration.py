"""Actual save migration matches the ORM and refuses losing durable fences."""

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import inspect, update

from app.models.agent_device_script_case_request import ScriptCaseSaveRequest
from app.services.agent_device_script_case_requests import ScriptCaseRequests
from tests.test_agent_device_script_binding import (  # noqa: F401
    database as binding_database,
)
from tests.test_agent_device_script_trials import trial_db  # noqa: F401
from tests.test_agent_device_script_cases import command, service
from tests.test_agent_device_script_cases import evidence_key  # noqa: F401


@pytest.mark.asyncio
async def test_save_migration_shape_roundtrip_and_history_guard(
    trial_db,  # noqa: F811
):  # noqa: F811
    sessions, project, actor, agent, _, owner, binding, _, engine = trial_db
    path = Path(__file__).parents[1] / (
        "migrations/versions/k9f0a1b2c3d4_script_case_save_requests.py"
    )
    spec = importlib.util.spec_from_file_location("owned_save_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def migrate(conn, action):
        with Operations.context(MigrationContext.configure(conn)):
            getattr(migration, action)()

    def check(conn):
        inspector, table = inspect(conn), ScriptCaseSaveRequest.__table__
        columns = {
            column["name"]: column
            for column in inspector.get_columns(table.name)
        }
        assert set(columns) == set(table.columns.keys())
        for column in table.columns:
            actual = columns[column.name]
            assert actual["nullable"] == column.nullable
            assert actual["type"].compile(dialect=conn.dialect) == (
                column.type.compile(dialect=conn.dialect)
            )
        for method, kind in (
            (inspector.get_check_constraints, "CheckConstraint"),
            (inspector.get_unique_constraints, "UniqueConstraint"),
        ):
            assert {item["name"] for item in method(table.name)} == {
                item.name
                for item in table.constraints
                if type(item).__name__ == kind
            }
        assert {
            tuple(item["constrained_columns"])
            for item in inspector.get_foreign_keys(table.name)
        } == {
            tuple(column.name for column in key.columns)
            for key in table.foreign_key_constraints
        }
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
        for action in ("downgrade", "upgrade"):
            await db.run_sync(migrate, action)
        await db.run_sync(check)
    async with sessions() as db:
        request = command(binding)
        row, _ = await ScriptCaseRequests(service(db, owner)).register(
            project, actor, agent, request, "a" * 64
        )
        for changes in (
            {
                "status": "failed",
                "completed_at": datetime.now(timezone.utc),
                "error_code": None,
            },
            {"status": "saved", "completed_at": datetime.now(timezone.utc)},
            {"status": "unsupported"},
            {"binding_revision": 0},
        ):
            # Check the database SQLSTATE, including driver-wrapped exceptions.
            with pytest.raises(Exception) as rejected:
                async with db.begin_nested():
                    await db.execute(
                        update(ScriptCaseSaveRequest)
                        .where(ScriptCaseSaveRequest.id == row.id)
                        .values(**changes)
                        .execution_options(synchronize_session=False)
                    )
            cause = getattr(rejected.value, "orig", rejected.value)
            assert getattr(cause, "sqlstate", None) == "23514"
            assert "ck_ai_script_case_save_" in str(rejected.value)
    with pytest.raises(RuntimeError, match="history exists"):
        async with engine.begin() as db:
            await db.run_sync(migrate, "downgrade")
    assert owner.posts == 0
