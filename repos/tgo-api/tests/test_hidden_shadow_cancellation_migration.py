"""Cancelled preregistrations stay auditable and release the active slot."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


def _migration(filename: str):
    path = Path(__file__).parents[1] / f"alembic/versions/{filename}"
    spec = spec_from_file_location(filename.removesuffix(".py"), path)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_cancel_migration_releases_slot_without_discarding_history():
    initial = _migration("0069_hidden_shadow_protocols.py")
    seed = _migration("0074_hidden_shadow_seed_commitment.py")
    cancellation = _migration("0075_hidden_shadow_cancellation.py")
    assert cancellation.down_revision == "0074_hidden_shadow_seed_commitment"
    engine = create_engine("sqlite://")
    project_id = uuid4().hex
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            initial.upgrade()
            seed.upgrade()
            cancellation.upgrade()
            columns = {
                column["name"] for column in inspect(connection)
                .get_columns("api_hidden_shadow_protocols")
            }
            assert {"cancelled_at", "cancelled_by"} <= columns

            def insert(request_id: str) -> None:
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
                    "id": uuid4().hex, "project": project_id,
                    "creator": uuid4().hex, "request": request_id,
                    "fingerprint": "a" * 64, "seed": "1a" * 16,
                })

            first_request = uuid4().hex
            insert(first_request)
            with pytest.raises(IntegrityError):
                insert(uuid4().hex)
            connection.execute(text(
                "UPDATE api_hidden_shadow_protocols SET "
                "cancelled_at = '2026-10-20 01:00:00', "
                "cancelled_by = :actor WHERE request_id = :request"
            ), {"actor": uuid4().hex, "request": first_request})
            insert(uuid4().hex)
            assert connection.execute(text(
                "SELECT COUNT(*) FROM api_hidden_shadow_protocols "
                "WHERE project_id = :project"
            ), {"project": project_id}).scalar_one() == 2
            with pytest.raises(RuntimeError, match="archive"):
                cancellation.downgrade()
    engine.dispose()


def test_cancel_migration_downgrades_when_no_cancellation_exists():
    initial = _migration("0069_hidden_shadow_protocols.py")
    seed = _migration("0074_hidden_shadow_seed_commitment.py")
    cancellation = _migration("0075_hidden_shadow_cancellation.py")
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            initial.upgrade()
            seed.upgrade()
            cancellation.upgrade()
            cancellation.downgrade()
            columns = {
                column["name"] for column in inspect(connection)
                .get_columns("api_hidden_shadow_protocols")
            }
            assert "cancelled_at" not in columns
            assert "cancelled_by" not in columns
            assert "uq_hidden_protocol_active_project" in {
                index["name"] for index in inspect(connection)
                .get_indexes("api_hidden_shadow_protocols")
            }
    engine.dispose()
