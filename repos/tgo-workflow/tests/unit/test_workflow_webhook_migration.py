"""A webhook or command fact must survive attempted destructive downgrade."""

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import create_engine, inspect, text

from app.models.workflow_webhook import WorkflowWebhook, WorkflowWebhookCommand


@pytest.mark.parametrize("protected", [None, "binding", "command"])
def test_upgrade_and_nonempty_downgrade_guard(protected):
    path = (
        Path(__file__).parents[2]
        / "migrations/versions/0020_workflow_webhooks.py"
    )
    spec = importlib.util.spec_from_file_location("webhook_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as db:
            db.execute(
                text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)")
            )
            db.execute(
                text("INSERT INTO wf_workflows VALUES ('original-flow')")
            )
            with Operations.context(MigrationContext.configure(db)):
                migration.upgrade()
                assert {
                    column["name"]
                    for column in inspect(db).get_columns(
                        "wf_workflow_webhooks"
                    )
                } == set(WorkflowWebhook.__table__.columns.keys())
                if protected == "binding":
                    db.execute(
                        WorkflowWebhook.__table__.insert().values(
                            id="test-hook",
                            project_id="project",
                            workflow_id="original-flow",
                            revision=1,
                            credential_revision=1,
                            key_id="synthetic",
                            credential_ciphertext="synthetic-ciphertext",
                            enabled=False,
                            release_id="release",
                            release_digest="a" * 64,
                            actor_staff_id="actor",
                            created_by="actor",
                            updated_by="actor",
                        )
                    )
                elif protected == "command":
                    db.execute(
                        WorkflowWebhookCommand.__table__.insert().values(
                            project_id="project",
                            request_id="original-request",
                            workflow_id="original-flow",
                            actor_staff_id="actor",
                            operation="disable",
                            request_digest="proof",
                            credential_issued=False,
                            result_snapshot={},
                        )
                    )
                if protected:
                    with pytest.raises(RuntimeError, match="请求回执"):
                        migration.downgrade()
                    assert (
                        "wf_workflow_webhooks" in inspect(db).get_table_names()
                    )
                else:
                    migration.downgrade()
                    assert (
                        "wf_workflow_webhooks"
                        not in inspect(db).get_table_names()
                    )
                assert (
                    db.scalar(text("SELECT count(*) FROM wf_workflows")) == 1
                )
    finally:
        engine.dispose()
