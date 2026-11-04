"""The independent protocol migration preserves frozen cases on rollback."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from app.models.hidden_shadow_protocol import HiddenShadowProtocolCase


def test_hidden_protocol_migration_guards_nonempty_manifest():
    path = Path(__file__).parents[1] / (
        "alembic/versions/0069_hidden_shadow_protocols.py"
    )
    spec = spec_from_file_location("hidden_protocol_migration", path)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0068_hidden_shadow_runs"
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                names = set(inspect(connection).get_table_names())
                assert {
                    "api_hidden_shadow_protocols",
                    "api_hidden_shadow_protocol_cases",
                } <= names
                columns = {
                    column["name"] for column in inspect(connection)
                    .get_columns("api_hidden_shadow_protocol_cases")
                }
                assert columns == set(
                    HiddenShadowProtocolCase.__table__.columns.keys()
                )
                connection.execute(text(
                    "INSERT INTO api_hidden_shadow_protocols "
                    "(id, project_id, creator_id, request_id, "
                    "request_fingerprint, window_start, window_end, "
                    "sample_size, algorithm, random_seed) "
                    "VALUES (:id, :project, :creator, :request, :fingerprint, "
                    "'2026-10-19 00:00:00', '2026-10-20 00:00:00', 1, "
                    "'sha256-seed-wecom-kf-independent-v3', :seed)"
                ), {
                    "id": uuid4().hex, "project": uuid4().hex,
                    "creator": uuid4().hex, "request": uuid4().hex,
                    "fingerprint": "a" * 64, "seed": "b" * 32,
                })
                with pytest.raises(RuntimeError, match="archive"):
                    migration.downgrade()
                connection.execute(text(
                    "DELETE FROM api_hidden_shadow_protocols"
                ))
                migration.downgrade()
                names = set(inspect(connection).get_table_names())
                assert "api_hidden_shadow_protocols" not in names
    finally:
        engine.dispose()
