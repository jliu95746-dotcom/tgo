"""Tool trace records cannot be silently removed by a schema rollback."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def _migration(filename: str):
    path = Path(__file__).parents[1] / "alembic/versions" / filename
    spec = spec_from_file_location(filename.removesuffix(".py"), path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tool_event_migration_preserves_nonempty_trace():
    attempt_migration = _migration("0064_ai_execution_attempts.py")
    tool_migration = _migration("0065_ai_execution_tool_events.py")
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                attempt_migration.upgrade()
                tool_migration.upgrade()
                columns = {
                    item["name"] for item in inspect(connection).get_columns(
                        "api_ai_execution_tool_events"
                    )
                }
                assert {"execution_id", "sequence_no", "tool_name", "status"} <= columns
                connection.execute(text(
                    "INSERT INTO api_ai_execution_tool_events "
                    "(id, project_id, execution_id, sequence_no, tool_name, status) "
                    "VALUES (:id, :project_id, :execution_id, "
                    "1, 'lookup_order', 'started')"
                ), {
                    "id": uuid4().hex, "project_id": uuid4().hex,
                    "execution_id": uuid4().hex,
                })
                with pytest.raises(RuntimeError, match="archive"):
                    tool_migration.downgrade()
                connection.execute(text("DELETE FROM api_ai_execution_tool_events"))
                tool_migration.downgrade()
                assert "api_ai_execution_tool_events" not in inspect(
                    connection
                ).get_table_names()
                attempt_migration.downgrade()
    finally:
        engine.dispose()
