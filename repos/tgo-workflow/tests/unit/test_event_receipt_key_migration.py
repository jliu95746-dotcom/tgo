"""Nullable legacy metadata preserves old proofs; downgrade protects new ones."""

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_receipt_key_migration_keeps_legacy_rows_and_blocks_lossy_downgrade(
    monkeypatch,
):
    path = (
        Path(__file__).parents[2]
        / "migrations/versions/0017_event_receipt_keys.py"
    )
    spec = importlib.util.spec_from_file_location(
        "receipt_key_migration", path
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE wf_workflow_executions (id VARCHAR PRIMARY KEY, invocation_input_hmac VARCHAR(64))"
            )
        )
        connection.execute(
            text(
                "INSERT INTO wf_workflow_executions VALUES ('legacy', 'keep-proof')"
            )
        )
        monkeypatch.setattr(
            migration, "op", Operations(MigrationContext.configure(connection))
        )
        migration.upgrade()
        assert connection.execute(
            text(
                "SELECT invocation_input_hmac, event_receipt_key_id FROM wf_workflow_executions"
            )
        ).one() == ("keep-proof", None)
        connection.execute(
            text(
                "INSERT INTO wf_workflow_executions VALUES ('modern','modern-proof','r1')"
            )
        )
        with pytest.raises(RuntimeError, match="版本化事件回执"):
            migration.downgrade()
        assert "event_receipt_key_id" in {
            c["name"]
            for c in inspect(connection).get_columns("wf_workflow_executions")
        }
        connection.execute(
            text("DELETE FROM wf_workflow_executions WHERE id='modern'")
        )
        migration.downgrade()
        assert (
            connection.execute(
                text(
                    "SELECT invocation_input_hmac FROM wf_workflow_executions"
                )
            ).scalar_one()
            == "keep-proof"
        )
    engine.dispose()
