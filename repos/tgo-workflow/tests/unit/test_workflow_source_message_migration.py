"""Old runs remain readable and traced source messages block lossy rollback."""

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text


def test_workflow_source_message_migration():
    path = Path(__file__).parents[2] / "migrations/versions/0012_workflow_source_message.py"
    spec = importlib.util.spec_from_file_location("source_message_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
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
                old = connection.execute(text(
                    "SELECT source_message_id, source_channel "
                    "FROM wf_workflow_executions WHERE id='legacy-run'"
                )).one()
                assert old == (None, None)
                connection.execute(text(
                    "INSERT INTO wf_workflow_executions "
                    "(id, project_id, source_message_id, source_channel) "
                    "VALUES ('traced-run', 'project-a', 'message-a', 'web')"
                ))
                with pytest.raises(RuntimeError, match="source message"):
                    migration.downgrade()
                connection.execute(text(
                    "DELETE FROM wf_workflow_executions WHERE id='traced-run'"
                ))
                migration.downgrade()
    finally:
        engine.dispose()
