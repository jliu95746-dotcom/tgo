"""Legacy terminal approvals lose continuation context but keep decisions."""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.services.approval_preview import approval_preview_digest


def load_migration():
    path = (
        Path(__file__).parents[2]
        / "migrations/versions/0016_terminal_approval_checkpoints.py"
    )
    spec = importlib.util.spec_from_file_location(
        "terminal_approval_checkpoints",
        path,
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def legacy_tables(metadata):
    runs = sa.Table(
        "wf_workflow_executions",
        metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("status", sa.String, nullable=False),
    )
    approvals = sa.Table(
        "wf_approval_requests",
        metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("execution_id", sa.String, nullable=False),
        sa.Column("summary", sa.String, nullable=False),
        sa.Column("checkpoint", sa.JSON, nullable=False),
        sa.Column("status", sa.String, nullable=False),
    )
    return runs, approvals


def test_terminal_checkpoints_scrubbed_in_batches_and_active_preserved(
    monkeypatch,
):
    migration = load_migration()
    migration.BATCH_SIZE = 2
    monkeypatch.setenv("TGO_WORKFLOW_INPUT_REDACTION_BACKUP_VERIFIED", "1")
    metadata = sa.MetaData()
    runs, approvals = legacy_tables(metadata)
    engine = sa.create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            connection.execute(
                runs.insert(),
                [
                    {"id": "run-1", "status": "completed"},
                    {"id": "run-2", "status": "rejected"},
                    {"id": "run-3", "status": "pending_resume"},
                ],
            )
            connection.execute(
                approvals.insert(),
                [
                    {
                        "id": "01",
                        "execution_id": "run-1",
                        "summary": "核对客户诉求",
                        "status": "approved",
                        "checkpoint": {
                            "context_data": {"question": "private-1"}
                        },
                    },
                    {
                        "id": "02",
                        "execution_id": "run-2",
                        "summary": "拒绝申请",
                        "status": "rejected",
                        "checkpoint": {
                            "context_data": {"question": "private-2"}
                        },
                    },
                    {
                        "id": "03",
                        "execution_id": "run-3",
                        "summary": "等待执行",
                        "status": "approved",
                        "checkpoint": {"context_data": {"question": "active"}},
                    },
                ],
            )
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                migration.upgrade()
                with pytest.raises(RuntimeError, match="cannot be reversed"):
                    migration.downgrade()
            saved = (
                connection.execute(
                    sa.select(approvals).order_by(approvals.c.id)
                )
                .mappings()
                .all()
            )
            assert saved[0]["checkpoint"] == {
                "capture_status": "redacted",
                "approval_preview_digest": approval_preview_digest(
                    "01",
                    "run-1",
                    "核对客户诉求",
                ),
            }
            assert saved[1]["checkpoint"] == {
                "capture_status": "redacted",
                "approval_preview_digest": approval_preview_digest(
                    "02",
                    "run-2",
                    "拒绝申请",
                ),
            }
            assert saved[2]["checkpoint"] == {
                "context_data": {"question": "active"},
            }
            assert [row["status"] for row in saved] == [
                "approved",
                "rejected",
                "approved",
            ]
            assert [row["summary"] for row in saved] == [
                "核对客户诉求",
                "拒绝申请",
                "等待执行",
            ]
            assert "private-1" not in str(saved)
            assert "private-2" not in str(saved)
    finally:
        engine.dispose()


def test_active_approval_needs_no_backup_acknowledgement(monkeypatch):
    migration = load_migration()
    monkeypatch.delenv(
        "TGO_WORKFLOW_INPUT_REDACTION_BACKUP_VERIFIED",
        raising=False,
    )
    metadata = sa.MetaData()
    runs, approvals = legacy_tables(metadata)
    engine = sa.create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            connection.execute(
                runs.insert().values(
                    id="run-1",
                    status="waiting_approval",
                )
            )
            connection.execute(
                approvals.insert().values(
                    id="01",
                    execution_id="run-1",
                    status="pending",
                    summary="核对客户诉求",
                    checkpoint={"context_data": {"question": "active"}},
                )
            )
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            assert connection.execute(
                sa.select(approvals.c.checkpoint)
            ).scalar_one() == {"context_data": {"question": "active"}}
    finally:
        engine.dispose()


def test_terminal_approvals_require_backup_acknowledgement(monkeypatch):
    migration = load_migration()
    monkeypatch.delenv(
        "TGO_WORKFLOW_INPUT_REDACTION_BACKUP_VERIFIED",
        raising=False,
    )
    metadata = sa.MetaData()
    runs, approvals = legacy_tables(metadata)
    engine = sa.create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            connection.execute(
                runs.insert().values(id="run-1", status="failed")
            )
            connection.execute(
                approvals.insert().values(
                    id="01",
                    execution_id="run-1",
                    status="approved",
                    summary="核对客户诉求",
                    checkpoint={"context_data": {"question": "private"}},
                )
            )
            with Operations.context(MigrationContext.configure(connection)):
                with pytest.raises(RuntimeError, match="BACKUP_VERIFIED"):
                    migration.upgrade()
            assert connection.execute(
                sa.select(approvals.c.checkpoint)
            ).scalar_one() == {"context_data": {"question": "private"}}
    finally:
        engine.dispose()
