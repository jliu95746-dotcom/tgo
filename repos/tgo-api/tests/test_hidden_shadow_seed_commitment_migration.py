"""A new G4 seed commitment must not be invented for existing protocols."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def _migration(filename: str):
    path = Path(__file__).parents[1] / f"alembic/versions/{filename}"
    spec = spec_from_file_location(filename.removesuffix(".py"), path)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_seed_commitment_migration_keeps_legacy_uncommitted():
    initial = _migration("0069_hidden_shadow_protocols.py")
    commitment = _migration("0074_hidden_shadow_seed_commitment.py")
    assert commitment.down_revision == (
        "0073_hidden_shadow_review_source_proof"
    )
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                initial.upgrade()
                protocol_id = uuid4().hex
                connection.execute(text(
                    "INSERT INTO api_hidden_shadow_protocols "
                    "(id, project_id, creator_id, request_id, "
                    "request_fingerprint, window_start, window_end, "
                    "sample_size, algorithm, random_seed, created_at) VALUES "
                    "(:id, :project, :creator, :request, :fingerprint, "
                    "'2026-10-21 00:00:00', '2026-10-22 00:00:00', "
                    "1, 'sha256-seed-wecom-kf-independent-v3', "
                    ":seed, '2026-10-20 00:00:00')"
                ), {
                    "id": protocol_id, "project": uuid4().hex,
                    "creator": uuid4().hex, "request": uuid4().hex,
                    "fingerprint": "a" * 64, "seed": "1a" * 16,
                })
                commitment.upgrade()
                assert "seed_sha256" in {
                    column["name"] for column in inspect(connection)
                    .get_columns("api_hidden_shadow_protocols")
                }
                assert connection.execute(text(
                    "SELECT seed_sha256 FROM api_hidden_shadow_protocols "
                    "WHERE id = :id"
                ), {"id": protocol_id}).scalar_one() is None
                connection.execute(text(
                    "UPDATE api_hidden_shadow_protocols SET "
                    "seed_sha256 = :digest WHERE id = :id"
                ), {"digest": "b" * 64, "id": protocol_id})
                with pytest.raises(RuntimeError, match="archive"):
                    commitment.downgrade()
                connection.execute(text(
                    "UPDATE api_hidden_shadow_protocols SET "
                    "seed_sha256 = NULL WHERE id = :id"
                ), {"id": protocol_id})
                commitment.downgrade()
                assert "seed_sha256" not in {
                    column["name"] for column in inspect(connection)
                    .get_columns("api_hidden_shadow_protocols")
                }
    finally:
        engine.dispose()
