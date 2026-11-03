"""The approval migration keeps old runs and refuses to discard pending work."""

import importlib.util
import io
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text


def migration():
    path = Path(__file__).parents[2] / "migrations/versions/0007_workflow_approval_state.py"
    spec = importlib.util.spec_from_file_location("workflow_approval_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_approval_migration_preserves_history_and_blocks_lossy_rollback():
    revision = migration()
    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE wf_workflow_executions "
                "(id VARCHAR PRIMARY KEY, project_id VARCHAR NOT NULL)"
            ))
            connection.execute(text(
                "INSERT INTO wf_workflow_executions (id, project_id) "
                "VALUES ('legacy-run', 'project-a')"
            ))
            with Operations.context(MigrationContext.configure(connection)):
                revision.upgrade()
                assert connection.execute(text(
                    "SELECT active_approval_id FROM wf_workflow_executions "
                    "WHERE id='legacy-run'"
                )).scalar_one() is None
                connection.execute(text(
                    "UPDATE wf_workflow_executions SET active_approval_id='approval-a' "
                    "WHERE id='legacy-run'"
                ))
                with pytest.raises(RuntimeError, match="active approval"):
                    revision.downgrade()
                connection.execute(text(
                    "UPDATE wf_workflow_executions SET active_approval_id=NULL "
                    "WHERE id='legacy-run'"
                ))
                connection.execute(text(
                    "INSERT INTO wf_approval_requests "
                    "(id, project_id, execution_id, node_id, approver_staff_id, "
                    "summary, checkpoint, status, expires_at) VALUES "
                    "('approval-a', 'project-a', 'legacy-run', 'approval', "
                    "'staff-a', '确认', '{}', 'pending', '2026-10-15 00:00:00')"
                ))
                with pytest.raises(RuntimeError, match="non-empty"):
                    revision.downgrade()
                connection.execute(text("DELETE FROM wf_approval_requests"))
                revision.downgrade()
                columns = connection.execute(text(
                    "PRAGMA table_info(wf_workflow_executions)"
                )).all()
                assert "active_approval_id" not in {column[1] for column in columns}
    finally:
        engine.dispose()


def test_approval_migration_compiles_postgresql_sql():
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": buffer},
    )
    with Operations.context(context):
        migration().upgrade()
    sql = buffer.getvalue()
    assert "CREATE TABLE wf_approval_requests" in sql
    assert "active_approval_id" in sql
    assert "CREATE INDEX ix_wf_approval_assignee_status" in sql
