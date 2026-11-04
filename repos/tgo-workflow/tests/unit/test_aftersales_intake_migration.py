"""Internal intake fields keep old review rows and protect prepared receipts."""

from importlib import import_module

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_intake_migration_keeps_old_reviews_and_refuses_receipt_loss(monkeypatch):
    migration = import_module("migrations.versions.0011_aftersales_intake")
    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE wf_aftersales_reviews (id VARCHAR PRIMARY KEY, status VARCHAR)"
            ))
            connection.execute(text(
                "INSERT INTO wf_aftersales_reviews (id, status) VALUES ('old', 'reviewed')"
            ))
            monkeypatch.setattr(
                migration, "op", Operations(MigrationContext.configure(connection)),
            )
            migration.upgrade()
            columns = {item["name"] for item in inspect(connection).get_columns(
                "wf_aftersales_reviews"
            )}
            assert {"intake_digest", "intake_evidence_file_ids", "intake_prepared_at"} <= columns
            assert connection.execute(text(
                "SELECT intake_digest FROM wf_aftersales_reviews WHERE id = 'old'"
            )).scalar_one_or_none() is None
            connection.execute(text(
                "UPDATE wf_aftersales_reviews SET intake_digest = :digest WHERE id = 'old'"
            ), {"digest": "a" * 64})
            with pytest.raises(RuntimeError, match="receipts"):
                migration.downgrade()
            connection.execute(text("DELETE FROM wf_aftersales_reviews"))
            migration.downgrade()
            remaining = {item["name"] for item in inspect(connection).get_columns(
                "wf_aftersales_reviews"
            )}
            assert "intake_digest" not in remaining
    finally:
        engine.dispose()
