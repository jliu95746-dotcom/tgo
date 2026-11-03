"""Legacy workflow pin history survives old-row upgrades and safe rollback checks."""

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from app.models.workflow_pin_transition import AgentWorkflowPinTransition


def load_migration():
    path = (
        Path(__file__).parents[1]
        / "migrations"
        / "versions"
        / "e3f4a5b6c7d8_workflow_pin_transitions.py"
    )
    spec = importlib.util.spec_from_file_location("workflow_pin_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_workflow_pin_migration_matches_model_and_preserves_audit_rows():
    migration = load_migration()
    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                migrated = {
                    column["name"]
                    for column in inspect(connection).get_columns(
                        "ai_agent_workflow_pin_transitions"
                    )
                }
                assert migrated == set(
                    AgentWorkflowPinTransition.__table__.columns.keys()
                )
                connection.execute(
                    text(
                        "INSERT INTO ai_agent_workflow_pin_transitions "
                        "(id, project_id, requested_by, request_id, agent_id, binding_id, "
                        "workflow_id, release_version, release_id, release_digest, "
                        "agent_active, binding_enabled) VALUES "
                        "(:id, :project, :actor, :request, :agent, :binding, "
                        ":workflow, 2, :release, :digest, 1, 0)"
                    ),
                    {
                        key: uuid4().hex
                        for key in (
                            "id",
                            "project",
                            "actor",
                            "request",
                            "agent",
                            "binding",
                            "workflow",
                            "release",
                        )
                    }
                    | {"digest": "b" * 64},
                )
                with pytest.raises(RuntimeError, match="history exists"):
                    migration.downgrade()
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM ai_agent_workflow_pin_transitions")
                    ).scalar_one()
                    == 1
                )
                connection.execute(
                    text("DELETE FROM ai_agent_workflow_pin_transitions")
                )
                migration.downgrade()
                assert (
                    "ai_agent_workflow_pin_transitions"
                    not in inspect(connection).get_table_names()
                )
    finally:
        engine.dispose()
