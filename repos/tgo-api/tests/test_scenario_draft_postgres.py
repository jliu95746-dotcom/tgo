"""Opt-in migration round trip and concurrent edits in an isolated PostgreSQL schema."""

import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError
from app.schemas.scenario_drafts import ScenarioDraftCreate, ScenarioDraftUpdate
from app.services.scenario_drafts import create_draft, get_draft, update_draft

TEST_URL = os.getenv("SCENARIOS_POSTGRES_TEST_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="An isolated scenario PostgreSQL test database is required")


def test_draft_migration_round_trip_preserves_parent_rows_and_resumes_config():
    assert TEST_URL is not None
    assert (make_url(TEST_URL).database or "").startswith("tgo_scenarios_test_")
    schema = f"scenario_drafts_{uuid4().hex}"
    engine = create_engine(make_url(TEST_URL).set(drivername="postgresql+psycopg2"))
    project, staff = uuid4(), uuid4()
    version = Path(__file__).parents[1] / "alembic/versions/0047_scenario_drafts.py"
    spec = importlib.util.spec_from_file_location("scenario_draft_migration", version)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    try:
        with engine.connect() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET search_path TO "{schema}"'))
            # Only the parent contracts required by this additive migration.
            connection.execute(text("CREATE TABLE api_projects (id uuid PRIMARY KEY)"))
            connection.execute(text("CREATE TABLE api_staff (id uuid PRIMARY KEY)"))
            connection.execute(text("INSERT INTO api_projects (id) VALUES (:id)"), {"id": project})
            connection.execute(text("INSERT INTO api_staff (id) VALUES (:id)"), {"id": staff})
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            connection.commit()
            with Session(connection) as session:
                created = create_draft(session, project, staff, ScenarioDraftCreate(
                    idempotency_key=uuid4(), template_id="knowledge", module="agents",
                    business={"name": "隔离测试岗位", "company_summary": "测试企业"},
                ))
                updated = update_draft(session, project, staff, created.id, ScenarioDraftUpdate(
                    expected_revision=1, step="resources", bindings={"knowledge_ids": [str(uuid4())]},
                ))
                assert updated.revision == 2
            with Session(connection) as reopened:
                restored = get_draft(reopened, project, staff, created.id)
                assert restored.business.company_summary == "测试企业"
                assert restored.bindings.knowledge_ids == updated.bindings.knowledge_ids
                with pytest.raises(ConflictError):
                    update_draft(reopened, project, staff, created.id, ScenarioDraftUpdate(expected_revision=1, step="testing"))
                reopened.rollback()
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
            connection.commit()
            assert not inspect(connection).has_table("api_scenario_drafts", schema=schema)
            assert connection.scalar(text("SELECT count(*) FROM api_projects")) == 1
            assert connection.scalar(text("SELECT count(*) FROM api_staff")) == 1
    finally:
        # This schema was created by this test in an explicitly named test database.
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
