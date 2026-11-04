"""Source coordinates remain nullable for history and block lossy rollback."""

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text


def load_migration():
    path = Path(__file__).parents[2] / "migrations/versions/0005_execution_origin.py"
    spec = importlib.util.spec_from_file_location("execution_origin_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_execution_origin_migration_preserves_history_and_blocks_data_loss():
    migration = load_migration()
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
                migration.upgrade()
                assert connection.execute(text(
                    "SELECT ai_request_id, business_request_id, visitor_session_id "
                    "FROM wf_workflow_executions WHERE id='legacy-run'"
                )).one() == (None, None, None)
                connection.execute(text(
                    "INSERT INTO wf_workflow_executions "
                    "(id, project_id, ai_request_id, business_request_id, visitor_session_id) "
                    "VALUES ('new-run', 'project-a', 'ai-run-a', 'business-a', 'session-a')"
                ))
                with pytest.raises(RuntimeError, match="origin"):
                    migration.downgrade()
                connection.execute(text("DELETE FROM wf_workflow_executions WHERE id='new-run'"))
                migration.downgrade()
                columns = connection.execute(text(
                    "PRAGMA table_info(wf_workflow_executions)"
                )).all()
                assert "ai_request_id" not in {column[1] for column in columns}
    finally:
        engine.dispose()
