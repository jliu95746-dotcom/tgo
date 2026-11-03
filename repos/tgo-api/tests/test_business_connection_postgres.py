"""Real migration and lock races use only a dedicated disposable database."""

import base64
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import os
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import SecretStr
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import ConflictError
from app.schemas.business_connections import (
    BusinessConnectionCreate,
    BusinessConnectionUpdate,
)
from app.services import business_connections as connections

TEST_URL = os.getenv("SCENARIOS_POSTGRES_TEST_URL")
pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="Explicit isolated scenario PostgreSQL database required"
)


def test_connection_migration_ciphertext_concurrent_creation_and_edits_preserve_history(
    monkeypatch,
):
    assert TEST_URL is not None and (make_url(TEST_URL).database or "").startswith(
        "tgo_scenarios_test_"
    )
    monkeypatch.setattr(
        settings,
        "BUSINESS_CONNECTION_CREDENTIAL_KEY",
        SecretStr(base64.urlsafe_b64encode(os.urandom(32)).decode()),
    )
    schema = "business_connections_" + uuid4().hex
    engine = create_engine(
        make_url(TEST_URL).set(drivername="postgresql+psycopg2"),
        connect_args={"options": f"-csearch_path={schema}"},
    )
    project, actor = uuid4(), uuid4()
    path = Path(__file__).parents[1] / "alembic/versions/0048_business_connections.py"
    spec = importlib.util.spec_from_file_location("business_connection_migration", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET search_path TO "{schema}"'))
            connection.execute(text("CREATE TABLE api_projects (id uuid PRIMARY KEY)"))
            connection.execute(text("CREATE TABLE api_staff (id uuid PRIMARY KEY)"))
            connection.execute(
                text("INSERT INTO api_projects(id) VALUES (:id)"), {"id": project}
            )
            connection.execute(
                text("INSERT INTO api_staff(id) VALUES (:id)"), {"id": actor}
            )
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
        engine.dispose()
        body = BusinessConnectionCreate(
            request_id=uuid4(),
            name="隔离业务连接",
            mode="http",
            configuration={
                "base_url": "https://orders.example.test",
                "auth_mode": "bearer",
            },
            credentials={"token": "postgres-fixture-credential"},
        )
        barrier = Barrier(2)

        def create_once():
            with Session(engine) as db:
                barrier.wait(timeout=15)
                return connections.create(db, project, actor, body).id

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(create_once) for _ in range(2)]
            identities = [future.result(timeout=20) for future in futures]
        assert identities[0] == identities[1]
        barrier = Barrier(2)

        def update_once(name):
            with Session(engine) as db:
                barrier.wait(timeout=15)
                try:
                    connections.update(
                        db,
                        project,
                        actor,
                        identities[0],
                        BusinessConnectionUpdate(expected_revision=1, name=name),
                    )
                    return "saved"
                except ConflictError:
                    db.rollback()
                    return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(update_once, name)
                for name in ("第一位管理员", "第二位管理员")
            ]
            assert sorted(future.result(timeout=20) for future in futures) == [
                "conflict",
                "saved",
            ]
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT count(*) FROM api_business_connections"))
                == 1
            )
            ciphertext = connection.scalar(
                text("SELECT credential_ciphertext FROM api_business_connections")
            )
            assert ciphertext and "postgres-fixture-credential" not in ciphertext
            with pytest.raises(RuntimeError, match="retained"):
                with Operations.context(MigrationContext.configure(connection)):
                    migration.downgrade()
            assert inspect(connection).has_table(
                "api_business_connections", schema=schema
            )
            assert connection.scalar(text("SELECT count(*) FROM api_projects")) == 1
            connection.execute(text("DELETE FROM api_business_connections"))
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
            connection.commit()
            assert not inspect(connection).has_table(
                "api_business_connections", schema=schema
            )
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
