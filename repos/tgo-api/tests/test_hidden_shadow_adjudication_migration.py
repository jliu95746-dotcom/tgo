"""Independent adjudication migration preserves nonempty decisions."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from app.models.hidden_shadow_adjudication import HiddenShadowAdjudication


def test_hidden_adjudication_migration_guards_nonempty_decisions():
    path = Path(__file__).parents[1] / (
        "alembic/versions/0071_hidden_shadow_adjudications.py"
    )
    spec = spec_from_file_location("hidden_adjudication_migration", path)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0070_hidden_shadow_case_reviews"
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                columns = {
                    column["name"] for column in inspect(connection)
                    .get_columns("api_hidden_shadow_adjudications")
                }
                assert columns == set(
                    HiddenShadowAdjudication.__table__.columns.keys()
                )
                connection.execute(text(
                    "INSERT INTO api_hidden_shadow_adjudications "
                    "(id, project_id, protocol_id, candidate_id, "
                    "adjudicator_id, request_id, request_fingerprint, "
                    "review_set_sha256, evidence_sha256, verdict, note) "
                    "VALUES (:id, :project, :protocol, :candidate, "
                    ":adjudicator, :request, :fingerprint, :reviews, "
                    ":evidence, 'acceptable', 'Reviewed')"
                ), {
                    "id": uuid4().hex, "project": uuid4().hex,
                    "protocol": uuid4().hex, "candidate": uuid4().hex,
                    "adjudicator": uuid4().hex, "request": uuid4().hex,
                    "fingerprint": "a" * 64, "reviews": "b" * 64,
                    "evidence": "c" * 64,
                })
                with pytest.raises(RuntimeError, match="archive"):
                    migration.downgrade()
                connection.execute(text(
                    "DELETE FROM api_hidden_shadow_adjudications"
                ))
                migration.downgrade()
                assert "api_hidden_shadow_adjudications" not in (
                    inspect(connection).get_table_names()
                )
    finally:
        engine.dispose()
