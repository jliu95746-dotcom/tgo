"""Visitor stop evidence survives upgrade and blocks a lossy downgrade."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def _migration():
    path = Path(__file__).parents[1] / (
        "alembic/versions/0076_visitor_end_receipts.py"
    )
    spec = spec_from_file_location("visitor_end_receipts_migration", path)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_visitor_end_receipts_migration_preserves_rows_and_guards_downgrade():
    migration = _migration()
    assert migration.down_revision == "0075_hidden_shadow_cancellation"
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE api_ai_interaction_runs (id INTEGER PRIMARY KEY)"
        ))
        connection.execute(text(
            "CREATE TABLE api_visitors (id INTEGER PRIMARY KEY)"
        ))
        connection.execute(text(
            "INSERT INTO api_ai_interaction_runs (id) VALUES (1)"
        ))
        connection.execute(text("INSERT INTO api_visitors (id) VALUES (1)"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert "stop_confirmed_at" in {
                column["name"] for column in inspect(connection).get_columns(
                    "api_ai_interaction_runs"
                )
            }
            assert "im_disconnect_requested_at" in {
                column["name"] for column in inspect(connection).get_columns(
                    "api_visitors"
                )
            }
            assert connection.execute(text(
                "SELECT id FROM api_ai_interaction_runs"
            )).scalar_one() == 1
            connection.execute(text(
                "UPDATE api_ai_interaction_runs SET "
                "stop_confirmed_at = '2026-10-20 00:00:00' WHERE id = 1"
            ))
            with pytest.raises(RuntimeError, match="archive"):
                migration.downgrade()
            connection.execute(text(
                "UPDATE api_ai_interaction_runs SET stop_confirmed_at = NULL"
            ))
            connection.execute(text(
                "UPDATE api_visitors SET "
                "im_disconnect_requested_at = '2026-10-20 00:00:00' "
                "WHERE id = 1"
            ))
            with pytest.raises(RuntimeError, match="archive"):
                migration.downgrade()
            connection.execute(text(
                "UPDATE api_visitors SET im_disconnect_requested_at = NULL"
            ))
            migration.downgrade()
            assert "stop_confirmed_at" not in {
                column["name"] for column in inspect(connection).get_columns(
                    "api_ai_interaction_runs"
                )
            }
            assert "im_disconnect_requested_at" not in {
                column["name"] for column in inspect(connection).get_columns(
                    "api_visitors"
                )
            }
    engine.dispose()
