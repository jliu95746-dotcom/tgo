"""Old terminal workflow inputs can be removed with protected receipts."""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import SecretStr

from app.services.execution_snapshot import input_receipt_hmac


def load_migration():
    path = (
        Path(__file__).parents[2]
        / "migrations/versions/0015_terminal_input_privacy.py"
    )
    spec = importlib.util.spec_from_file_location(
        "terminal_input_privacy", path
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def legacy_table(metadata):
    return sa.Table(
        "wf_workflow_executions",
        metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("employee_agent_id", sa.String),
        sa.Column("input", sa.JSON),
    )


def test_terminal_inputs_are_scrubbed_and_active_inputs_remain(monkeypatch):
    migration = load_migration()
    migration.BATCH_SIZE = 2
    monkeypatch.setattr(
        migration.settings, "SECRET_KEY", SecretStr("synthetic-key")
    )
    monkeypatch.setenv("TGO_WORKFLOW_INPUT_REDACTION_BACKUP_VERIFIED", "1")
    metadata = sa.MetaData()
    legacy = legacy_table(metadata)
    engine = sa.create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            connection.execute(
                legacy.insert(),
                [
                    {
                        "id": "01",
                        "status": "completed",
                        "employee_agent_id": "employee-1",
                        "input": {"question": "private-question"},
                    },
                    {
                        "id": "02",
                        "status": "failed",
                        "employee_agent_id": None,
                        "input": {"question": "private-draft"},
                    },
                    {
                        "id": "03",
                        "status": "pending_resume",
                        "employee_agent_id": "employee-1",
                        "input": {"question": "active-question"},
                    },
                ],
            )
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                with pytest.raises(RuntimeError, match="cannot be reversed"):
                    migration.downgrade()
            upgraded = sa.table(
                "wf_workflow_executions",
                sa.column("id", sa.String),
                sa.column("input", sa.JSON),
                sa.column("invocation_input_hmac", sa.String),
            )
            rows = (
                connection.execute(sa.select(upgraded).order_by(upgraded.c.id))
                .mappings()
                .all()
            )
            assert rows[0]["input"] is None
            assert rows[0]["invocation_input_hmac"] == input_receipt_hmac(
                {"question": "private-question"}, "synthetic-key", "01"
            )
            assert rows[1]["input"] is None
            assert rows[1]["invocation_input_hmac"] is None
            assert rows[2]["input"] == {"question": "active-question"}
            assert rows[2]["invocation_input_hmac"] == input_receipt_hmac(
                {"question": "active-question"}, "synthetic-key", "03"
            )
    finally:
        engine.dispose()


def test_existing_terminal_rows_require_backup_acknowledgement(monkeypatch):
    migration = load_migration()
    monkeypatch.delenv(
        "TGO_WORKFLOW_INPUT_REDACTION_BACKUP_VERIFIED", raising=False
    )
    monkeypatch.setattr(
        migration.settings, "SECRET_KEY", SecretStr("synthetic-key")
    )
    metadata = sa.MetaData()
    legacy = legacy_table(metadata)
    engine = sa.create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            connection.execute(
                legacy.insert().values(
                    id="01",
                    status="completed",
                    input={"question": "private-question"},
                )
            )
            with Operations.context(MigrationContext.configure(connection)):
                with pytest.raises(RuntimeError, match="BACKUP_VERIFIED"):
                    migration.upgrade()
            assert connection.execute(
                sa.select(legacy.c.input)
            ).scalar_one() == {"question": "private-question"}
    finally:
        engine.dispose()


def test_employee_rows_require_a_configured_hmac_key(monkeypatch):
    migration = load_migration()
    monkeypatch.setattr(migration.settings, "SECRET_KEY", None)
    metadata = sa.MetaData()
    legacy = legacy_table(metadata)
    engine = sa.create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            connection.execute(
                legacy.insert().values(
                    id="01",
                    status="pending",
                    employee_agent_id="employee-1",
                    input={"question": "active-question"},
                )
            )
            with Operations.context(MigrationContext.configure(connection)):
                with pytest.raises(RuntimeError, match="SECRET_KEY"):
                    migration.upgrade()
            assert connection.execute(
                sa.select(legacy.c.input)
            ).scalar_one() == {"question": "active-question"}
    finally:
        engine.dispose()


def test_empty_installation_needs_no_backup_acknowledgement_or_key(
    monkeypatch,
):
    migration = load_migration()
    monkeypatch.delenv(
        "TGO_WORKFLOW_INPUT_REDACTION_BACKUP_VERIFIED", raising=False
    )
    monkeypatch.setattr(migration.settings, "SECRET_KEY", None)
    metadata = sa.MetaData()
    legacy_table(metadata)
    engine = sa.create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
    finally:
        engine.dispose()
