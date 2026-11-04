"""Legacy runs are readable and recovery state cannot be silently discarded."""

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text


def test_initial_dispatch_recovery_migration():
    path = Path(__file__).parents[2] / "migrations/versions/0013_initial_dispatch_recovery.py"
    spec = importlib.util.spec_from_file_location("initial_dispatch_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE wf_workflow_executions "
                "(id VARCHAR PRIMARY KEY, status VARCHAR, execution_mode VARCHAR)"
            ))
            connection.execute(text(
                "INSERT INTO wf_workflow_executions (id, status, execution_mode) "
                "VALUES ('legacy-run', 'completed', 'draft_test')"
            ))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                legacy = connection.execute(text(
                    "SELECT initial_dispatch_attempts, last_initial_dispatch_at "
                    "FROM wf_workflow_executions WHERE id='legacy-run'"
                )).one()
                assert legacy == (0, None)
                connection.execute(text(
                    "UPDATE wf_workflow_executions SET initial_dispatch_attempts=1 "
                    "WHERE id='legacy-run'"
                ))
                with pytest.raises(RuntimeError, match="dispatch recovery"):
                    migration.downgrade()
                connection.execute(text(
                    "UPDATE wf_workflow_executions SET initial_dispatch_attempts=0 "
                    "WHERE id='legacy-run'"
                ))
                migration.downgrade()
    finally:
        engine.dispose()
