"""The nullable employee identity migration preserves old runs and blocks unsafe rollback."""

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text


def load_migration():
    path = (
        Path(__file__).parents[2]
        / "migrations"
        / "versions"
        / "0004_employee_execution_binding.py"
    )
    spec = importlib.util.spec_from_file_location("employee_binding_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_employee_binding_migration_keeps_legacy_rows_and_rejects_data_loss():
    migration = load_migration()
    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE wf_workflow_executions (id VARCHAR PRIMARY KEY)")
            )
            connection.execute(
                text("INSERT INTO wf_workflow_executions (id) VALUES ('legacy-run')")
            )
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                assert (
                    connection.execute(
                        text(
                            "SELECT employee_agent_id FROM wf_workflow_executions "
                            "WHERE id='legacy-run'"
                        )
                    ).scalar_one()
                    is None
                )
                connection.execute(
                    text(
                        "INSERT INTO wf_workflow_executions (id, employee_agent_id) "
                        "VALUES ('employee-run', 'agent-1')"
                    )
                )
                with pytest.raises(RuntimeError, match="employee"):
                    migration.downgrade()
                assert (
                    connection.execute(
                        text(
                            "SELECT employee_agent_id FROM wf_workflow_executions "
                            "WHERE id='employee-run'"
                        )
                    ).scalar_one()
                    == "agent-1"
                )
                connection.execute(
                    text(
                        "UPDATE wf_workflow_executions SET employee_agent_id=NULL "
                        "WHERE id='employee-run'"
                    )
                )
                migration.downgrade()
                columns = connection.execute(
                    text("PRAGMA table_info(wf_workflow_executions)")
                ).all()
                assert "employee_agent_id" not in {column[1] for column in columns}
    finally:
        engine.dispose()
