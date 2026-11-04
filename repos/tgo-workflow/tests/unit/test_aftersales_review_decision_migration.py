"""The decision migration keeps prior tasks and refuses to discard decisions."""

from importlib import import_module

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_decision_migration_adds_fields_and_guards_downgrade(monkeypatch):
    migration = import_module("migrations.versions.0010_aftersales_review_decision")
    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE wf_aftersales_reviews (id VARCHAR PRIMARY KEY, status VARCHAR)"
            ))
            connection.execute(text(
                "INSERT INTO wf_aftersales_reviews (id, status) VALUES ('old', 'pending_staff_review')"
            ))
            monkeypatch.setattr(
                migration, "op", Operations(MigrationContext.configure(connection)),
            )
            migration.upgrade()
            columns = {column["name"] for column in inspect(connection).get_columns(
                "wf_aftersales_reviews"
            )}
            assert {"review_result", "reviewed_by", "reviewed_at"} <= columns
            assert connection.execute(text(
                "SELECT status FROM wf_aftersales_reviews WHERE id = 'old'"
            )).scalar_one() == "pending_staff_review"
            connection.execute(text(
                "UPDATE wf_aftersales_reviews SET status = 'reviewed' WHERE id = 'old'"
            ))
            with pytest.raises(RuntimeError, match="decisions"):
                migration.downgrade()
            connection.execute(text("DELETE FROM wf_aftersales_reviews"))
            migration.downgrade()
            remaining = {column["name"] for column in inspect(connection).get_columns(
                "wf_aftersales_reviews"
            )}
            assert "review_result" not in remaining
    finally:
        engine.dispose()
