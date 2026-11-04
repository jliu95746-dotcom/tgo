"""Independent review migration retains nonempty audit records."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from app.models.hidden_shadow_review import HiddenShadowCaseReview


def test_hidden_review_migration_refuses_nonempty_downgrade():
    path = Path(__file__).parents[1] / (
        "alembic/versions/0070_hidden_shadow_case_reviews.py"
    )
    spec = spec_from_file_location("hidden_review_migration", path)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0069_hidden_shadow_protocols"
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                assert "api_hidden_shadow_case_reviews" in (
                    inspect(connection).get_table_names()
                )
                migrated_columns = {
                    column["name"] for column in inspect(connection)
                    .get_columns("api_hidden_shadow_case_reviews")
                }
                later_proof_columns = {
                    "source_verified_at", "source_message_seq",
                    "source_content_sha256",
                }
                assert migrated_columns == (
                    set(HiddenShadowCaseReview.__table__.columns.keys())
                    - later_proof_columns
                )
                connection.execute(text(
                    "INSERT INTO api_hidden_shadow_case_reviews "
                    "(id, project_id, protocol_id, candidate_id, "
                    "reviewer_id, request_id, request_fingerprint, "
                    "evidence_sha256, verdict) VALUES "
                    "(:id, :project, :protocol, :candidate, :reviewer, "
                    ":request, :fingerprint, :evidence, 'acceptable')"
                ), {
                    "id": uuid4().hex, "project": uuid4().hex,
                    "protocol": uuid4().hex, "candidate": uuid4().hex,
                    "reviewer": uuid4().hex, "request": uuid4().hex,
                    "fingerprint": "a" * 64, "evidence": "b" * 64,
                })
                with pytest.raises(RuntimeError, match="archive"):
                    migration.downgrade()
                connection.execute(text(
                    "DELETE FROM api_hidden_shadow_case_reviews"
                ))
                migration.downgrade()
                assert "api_hidden_shadow_case_reviews" not in (
                    inspect(connection).get_table_names()
                )
    finally:
        engine.dispose()
