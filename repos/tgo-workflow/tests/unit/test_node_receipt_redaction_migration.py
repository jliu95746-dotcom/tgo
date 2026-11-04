"""Legacy node receipts are scrubbed without changing final answers."""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def load_migration():
    path = (
        Path(__file__).parents[2]
        / "migrations/versions/0014_node_receipt_redaction.py"
    )
    spec = importlib.util.spec_from_file_location(
        "node_receipt_redaction", path
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_legacy_node_receipts_are_scrubbed_in_batches_and_repeatably(
    monkeypatch,
):
    monkeypatch.setenv("TGO_NODE_RECEIPT_REDACTION_BACKUP_VERIFIED", "1")
    migration = load_migration()
    migration.BATCH_SIZE = 2
    metadata = sa.MetaData()
    nodes = sa.Table(
        "wf_node_executions",
        metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("node_type", sa.String, nullable=False),
        sa.Column("input", sa.JSON),
        sa.Column("output", sa.JSON),
        sa.Column("error", sa.Text),
    )
    engine = sa.create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    query_id = "2a1ab64c-8833-4cad-bc1e-799d26b2505a"
    connection_id = "0ddba593-b4af-4855-a1c5-a64c2822be38"
    child_id = "c28e3699-418d-472f-81f5-2b0de9353b17"
    try:
        with engine.begin() as connection:
            connection.execute(
                nodes.insert(),
                [
                    {
                        "id": "01",
                        "node_type": "input",
                        "input": {"email": "private@example.test"},
                        "output": {"email": "private@example.test"},
                        "error": "private-provider-error",
                    },
                    {
                        "id": "02",
                        "node_type": "business_query",
                        "input": None,
                        "output": {
                            "query_id": query_id,
                            "connection_id": connection_id,
                            "operation": "order_query",
                            "body": "private-order-data",
                        },
                        "error": None,
                    },
                    {
                        "id": "03",
                        "node_type": "answer",
                        "input": {"question": "private-question"},
                        "output": {"result": "客户可见答复"},
                        "error": None,
                    },
                    {
                        "id": "04",
                        "node_type": "agent",
                        "input": {"capture_status": "redacted"},
                        "output": {
                            "capture_status": "redacted",
                            "field_count": 3,
                            "agent_execution_id": child_id,
                            "private": "hidden",
                        },
                        "error": None,
                    },
                ],
            )
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                first = (
                    connection.execute(sa.select(nodes).order_by(nodes.c.id))
                    .mappings()
                    .all()
                )
                migration.upgrade()
                second = (
                    connection.execute(sa.select(nodes).order_by(nodes.c.id))
                    .mappings()
                    .all()
                )
                migration.downgrade()
                after_downgrade = (
                    connection.execute(sa.select(nodes).order_by(nodes.c.id))
                    .mappings()
                    .all()
                )
            assert first == second == after_downgrade
            assert first[0]["input"] == {
                "capture_status": "redacted",
                "field_count": 1,
            }
            assert first[0]["output"] == {
                "capture_status": "redacted",
                "field_count": 1,
            }
            assert first[0]["error"] == "工作流节点执行失败。"
            assert first[1]["output"] == {
                "query_id": query_id,
                "connection_id": connection_id,
                "operation": "order_query",
            }
            assert first[2]["output"] == {"result": "客户可见答复"}
            assert first[3]["output"] == {
                "capture_status": "redacted",
                "field_count": 3,
                "agent_execution_id": child_id,
            }
            assert "private@example.test" not in str(first)
            assert "private-provider-error" not in str(first)
            assert "private-order-data" not in str(first)
    finally:
        engine.dispose()


def test_existing_receipts_require_explicit_backup_acknowledgement(
    monkeypatch,
):
    monkeypatch.delenv(
        "TGO_NODE_RECEIPT_REDACTION_BACKUP_VERIFIED", raising=False
    )
    migration = load_migration()
    metadata = sa.MetaData()
    nodes = sa.Table(
        "wf_node_executions",
        metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("node_type", sa.String, nullable=False),
        sa.Column("input", sa.JSON),
        sa.Column("output", sa.JSON),
        sa.Column("error", sa.Text),
    )
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(
                nodes.insert().values(
                    id="01",
                    node_type="input",
                    input={"private": "secret"},
                )
            )
            with Operations.context(MigrationContext.configure(connection)):
                with pytest.raises(RuntimeError, match="BACKUP_VERIFIED"):
                    migration.upgrade()
            row = connection.execute(sa.select(nodes.c.input)).scalar_one()
            assert row == {"private": "secret"}
    finally:
        engine.dispose()


def test_empty_node_table_can_upgrade_without_backup_acknowledgement(
    monkeypatch,
):
    monkeypatch.delenv(
        "TGO_NODE_RECEIPT_REDACTION_BACKUP_VERIFIED", raising=False
    )
    migration = load_migration()
    metadata = sa.MetaData()
    sa.Table(
        "wf_node_executions",
        metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("node_type", sa.String, nullable=False),
        sa.Column("input", sa.JSON),
        sa.Column("output", sa.JSON),
        sa.Column("error", sa.Text),
    )
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        metadata.create_all(engine)
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
    finally:
        engine.dispose()
