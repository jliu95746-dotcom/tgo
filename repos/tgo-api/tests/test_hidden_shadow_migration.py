"""The hidden candidate table has an auditable and guarded migration."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_hidden_candidate_migration_refuses_nonempty_downgrade():
    path = Path(__file__).parents[1] / "alembic/versions/0068_hidden_shadow_runs.py"
    spec = spec_from_file_location("hidden_shadow_migration", path)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0067_ai_execution_business_queries"

    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                columns = {
                    column["name"] for column in inspect(connection).get_columns(
                        "api_hidden_shadow_runs"
                    )
                }
                assert {
                    "source_content_sha256", "source_verified_at",
                    "generation_version", "answer_sha256",
                    "lease_token", "redacted_at", "knowledge_evidence_sha256",
                    "model_name", "configured_prompt_sha256", "ai_execution_id",
                } <= columns
                connection.execute(text(
                    "INSERT INTO api_hidden_shadow_runs "
                    "(id, project_id, platform_id, visitor_id, source_message_id, "
                    "source_message_seq, source_verified_at, "
                    "source_content_sha256, generation_version, "
                    "status) VALUES (:id, :project, :platform, :visitor, 'source-1', "
                    "1, CURRENT_TIMESTAMP, :source_hash, "
                    "'wecom-kf-knowledge-only-v1', 'blocked')"
                ), {
                    "id": uuid4().hex, "project": uuid4().hex,
                    "platform": uuid4().hex, "visitor": uuid4().hex,
                    "source_hash": "a" * 64,
                })
                with pytest.raises(RuntimeError, match="archive"):
                    migration.downgrade()
                connection.execute(text("DELETE FROM api_hidden_shadow_runs"))
                migration.downgrade()
                assert "api_hidden_shadow_runs" not in inspect(
                    connection
                ).get_table_names()
    finally:
        engine.dispose()
