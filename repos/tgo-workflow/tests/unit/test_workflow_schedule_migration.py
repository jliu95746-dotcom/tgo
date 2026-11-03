"""An audit-bearing timer table can never be dropped by downgrade."""

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from app.config import settings


def test_upgrade_and_nonempty_downgrade_guard() -> None:
    source = (
        Path(__file__).parents[2]
        / "migrations/versions/0018_workflow_schedules.py"
    )
    spec = importlib.util.spec_from_file_location("schedule_migration", source)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as db:
            db.execute(
                text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)")
            )
            db.execute(text("INSERT INTO wf_workflows VALUES ('owned-flow')"))
            with Operations.context(MigrationContext.configure(db)):
                migration.upgrade()
                assert "wf_workflow_schedules" in inspect(db).get_table_names()
                db.execute(
                    text(
                        "INSERT INTO wf_workflow_schedule_commands "
                        "(project_id,request_id,workflow_id,actor_staff_id,"
                        "operation,request_digest,result_snapshot) "
                        "VALUES ('test','original-request','owned-flow',"
                        "'actor','disable','proof','{}')"
                    )
                )
                with pytest.raises(RuntimeError, match="请求回执"):
                    migration.downgrade()
                assert (
                    db.scalar(
                        text(
                            "SELECT count(*) FROM "
                            "wf_workflow_schedule_commands"
                        )
                    )
                    == 1
                )
                db.execute(
                    text(
                        "DELETE FROM wf_workflow_schedule_commands "
                        "WHERE request_id='original-request'"
                    )
                )
                migration.downgrade()
                assert (
                    "wf_workflow_schedules"
                    not in inspect(db).get_table_names()
                )
                assert (
                    db.scalar(text("SELECT count(*) FROM wf_workflows")) == 1
                )
    finally:
        engine.dispose()


def test_supplied_connection_migrates_only_its_isolated_database(
    monkeypatch,
) -> None:
    import asyncio

    monkeypatch.setattr(
        settings, "DATABASE_URL", "sqlite+aiosqlite:///:memory:"
    )

    def refuse_second_loop(coroutine):
        coroutine.close()
        raise RuntimeError("Supplied migration connection was ignored")

    monkeypatch.setattr(asyncio, "run", refuse_second_loop)
    root = Path(__file__).parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as db:
            db.execute(
                text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)")
            )
            db.execute(
                text(
                    "CREATE TABLE alembic_version "
                    "(version_num VARCHAR(128) PRIMARY KEY)"
                )
            )
            db.execute(
                text(
                    "INSERT INTO alembic_version "
                    "VALUES ('0017_event_receipt_keys')"
                )
            )
            config.attributes["connection"] = db
            command.upgrade(config, "0018_workflow_schedules")
            assert "wf_workflow_schedules" in inspect(db).get_table_names()
            assert (
                db.scalar(text("SELECT version_num FROM alembic_version"))
                == "0018_workflow_schedules"
            )
    finally:
        engine.dispose()
