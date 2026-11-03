"""Independent G4 signoff migration preserves nonempty decisions."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from app.models.hidden_shadow_gate_signoff import HiddenShadowGateSignoff


def test_hidden_gate_signoff_migration_guards_nonempty_records():
    path = Path(__file__).parents[1] / (
        "alembic/versions/0072_hidden_shadow_gate_signoffs.py"
    )
    spec = spec_from_file_location("hidden_gate_signoff_migration", path)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0071_hidden_shadow_adjudications"
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                columns = {
                    column["name"] for column in inspect(connection)
                    .get_columns("api_hidden_shadow_gate_signoffs")
                }
                assert columns == set(
                    HiddenShadowGateSignoff.__table__.columns.keys()
                )
                connection.execute(text(
                    "INSERT INTO api_hidden_shadow_gate_signoffs "
                    "(id, project_id, protocol_id, requester_id, request_id, "
                    "request_fingerprint, manifest_sha256, "
                    "review_evidence_sha256, "
                    "sample_count, evidence_reference, live_case_count, note) "
                    "VALUES (:id, :project, :protocol, :requester, :request, "
                    ":fingerprint, :manifest, :reviews, 1, 'audit/1', 1, "
                    "'Reviewed live evidence')"
                ), {
                    "id": uuid4().hex, "project": uuid4().hex,
                    "protocol": uuid4().hex, "requester": uuid4().hex,
                    "request": uuid4().hex, "fingerprint": "a" * 64,
                    "manifest": "b" * 64, "reviews": "c" * 64,
                })
                with pytest.raises(RuntimeError, match="archive"):
                    migration.downgrade()
                connection.execute(text(
                    "DELETE FROM api_hidden_shadow_gate_signoffs"
                ))
                migration.downgrade()
                assert "api_hidden_shadow_gate_signoffs" not in (
                    inspect(connection).get_table_names()
                )
    finally:
        engine.dispose()
