"""The execution-attempt migration must retain existing trace data."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_ai_execution_attempt_migration_rejects_data_loss_on_downgrade():
    path = Path(__file__).parents[1] / "alembic/versions/0064_ai_execution_attempts.py"
    spec = spec_from_file_location("ai_execution_attempt_migration", path)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                columns = {
                    column["name"]
                    for column in inspect(connection).get_columns(
                        "api_ai_execution_attempts"
                    )
                }
                assert {
                    "execution_id", "project_id", "interaction_run_id",
                    "response_client_msg_no", "status", "started_at", "completed_at",
                } == columns
                connection.execute(text(
                    "INSERT INTO api_ai_execution_attempts "
                    "(execution_id, project_id, interaction_run_id, "
                    "response_client_msg_no, status) "
                    "VALUES (:execution_id, :project_id, :interaction_run_id, "
                    "'reply-1', 'running')"
                ), {
                    "execution_id": uuid4().hex,
                    "project_id": uuid4().hex,
                    "interaction_run_id": uuid4().hex,
                })
                with pytest.raises(RuntimeError, match="archive"):
                    migration.downgrade()
                connection.execute(text("DELETE FROM api_ai_execution_attempts"))
                migration.downgrade()
                assert "api_ai_execution_attempts" not in inspect(
                    connection
                ).get_table_names()
    finally:
        engine.dispose()
