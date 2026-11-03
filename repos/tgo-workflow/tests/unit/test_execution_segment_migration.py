"""A timestamp is never fabricated or discarded by schema migration."""

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text


def test_segment_migration_preserves_legacy_start_and_guards_history():
    path = (
        Path(__file__).parents[2]
        / "migrations/versions/0022_execution_segments.py"
    )
    spec = importlib.util.spec_from_file_location("segment_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0021_webhook_deliveries"
    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE wf_workflow_executions "
                    "(id VARCHAR PRIMARY KEY, started_at DATETIME)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO wf_workflow_executions VALUES "
                    "('legacy', '2026-10-01 12:00:00')"
                )
            )
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                assert connection.execute(
                    text(
                        "SELECT started_at, active_segment_started_at "
                        "FROM wf_workflow_executions"
                    )
                ).one() == ("2026-10-01 12:00:00", None)
                migration.downgrade()
                migration.upgrade()
                connection.execute(
                    text(
                        "UPDATE wf_workflow_executions SET "
                        "active_segment_started_at='2026-10-25 12:00:00'"
                    )
                )
                with pytest.raises(RuntimeError, match="segment history"):
                    migration.downgrade()
                assert (
                    connection.scalar(
                        text(
                            "SELECT active_segment_started_at "
                            "FROM wf_workflow_executions"
                        )
                    )
                    == "2026-10-25 12:00:00"
                )
    finally:
        engine.dispose()
