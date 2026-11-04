"""The IM claim columns are reversible only when no claim is in flight."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_im_disconnect_lease_migration_guards_active_claims():
    path = Path(__file__).parents[1] / (
        "alembic/versions/0077_visitor_im_disconnect_lease.py"
    )
    spec = spec_from_file_location("visitor_im_lease_migration", path)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0076_visitor_end_receipts"

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE api_visitors (id INTEGER PRIMARY KEY)"
        ))
        connection.execute(text("INSERT INTO api_visitors (id) VALUES (1)"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            names = {
                column["name"] for column in inspect(connection).get_columns(
                    "api_visitors"
                )
            }
            assert {"im_disconnect_lease_until",
                    "im_disconnect_lease_token"} <= names
            connection.execute(text(
                "UPDATE api_visitors SET "
                "im_disconnect_lease_until = '2026-10-20 00:00:00', "
                "im_disconnect_lease_token = "
                "'00000000000000000000000000000001' WHERE id = 1"
            ))
            with pytest.raises(RuntimeError, match="leases exist"):
                migration.downgrade()
            connection.execute(text(
                "UPDATE api_visitors SET im_disconnect_lease_until = NULL, "
                "im_disconnect_lease_token = NULL WHERE id = 1"
            ))
            migration.downgrade()
            names = {
                column["name"] for column in inspect(connection).get_columns(
                    "api_visitors"
                )
            }
            assert "im_disconnect_lease_until" not in names
            assert "im_disconnect_lease_token" not in names
    engine.dispose()
