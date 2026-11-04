"""Review source proof migration preserves existing rows and refuses loss."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from app.models.hidden_shadow_review import HiddenShadowCaseReview


def _migration(filename: str):
    path = Path(__file__).parents[1] / f"alembic/versions/{filename}"
    spec = spec_from_file_location(filename.removesuffix(".py"), path)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_source_proof_migration_keeps_legacy_rows_and_guards_downgrade():
    initial = _migration("0070_hidden_shadow_case_reviews.py")
    proof = _migration("0073_hidden_shadow_review_source_proof.py")
    assert proof.down_revision == "0072_hidden_shadow_gate_signoffs"
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                initial.upgrade()
                review_id = uuid4().hex
                connection.execute(text(
                    "INSERT INTO api_hidden_shadow_case_reviews "
                    "(id, project_id, protocol_id, candidate_id, "
                    "reviewer_id, request_id, request_fingerprint, "
                    "evidence_sha256, verdict) VALUES "
                    "(:id, :project, :protocol, :candidate, :reviewer, "
                    ":request, :fingerprint, :evidence, 'acceptable')"
                ), {
                    "id": review_id, "project": uuid4().hex,
                    "protocol": uuid4().hex, "candidate": uuid4().hex,
                    "reviewer": uuid4().hex, "request": uuid4().hex,
                    "fingerprint": "a" * 64, "evidence": "b" * 64,
                })
                proof.upgrade()
                columns = {
                    column["name"] for column in inspect(connection)
                    .get_columns("api_hidden_shadow_case_reviews")
                }
                assert columns == set(
                    HiddenShadowCaseReview.__table__.columns.keys()
                )
                row = connection.execute(text(
                    "SELECT source_verified_at, source_message_seq, "
                    "source_content_sha256 FROM "
                    "api_hidden_shadow_case_reviews WHERE id = :id"
                ), {"id": review_id}).one()
                assert row == (None, None, None)
                connection.execute(text(
                    "UPDATE api_hidden_shadow_case_reviews SET "
                    "source_verified_at = CURRENT_TIMESTAMP, "
                    "source_message_seq = 42, source_content_sha256 = :digest "
                    "WHERE id = :id"
                ), {"digest": "c" * 64, "id": review_id})
                with pytest.raises(RuntimeError, match="archive"):
                    proof.downgrade()
                connection.execute(text(
                    "UPDATE api_hidden_shadow_case_reviews SET "
                    "source_verified_at = NULL, source_message_seq = NULL, "
                    "source_content_sha256 = NULL WHERE id = :id"
                ), {"id": review_id})
                proof.downgrade()
                assert "source_message_seq" not in {
                    column["name"] for column in inspect(connection)
                    .get_columns("api_hidden_shadow_case_reviews")
                }
    finally:
        engine.dispose()
