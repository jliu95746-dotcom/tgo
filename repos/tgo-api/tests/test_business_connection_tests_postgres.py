"""Actual migrations, independent transactions and late-result recovery."""

import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
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
from app.models.project import Project
from app.models.staff import Staff
from app.models.business_connection_test import BusinessConnectionTest
from app.schemas.business_connections import BusinessConnectionCreate
from app.schemas.business_connection_tests import BusinessConnectionTestRequest
from app.schemas.connection_test_protocol import (
    ConnectionTestReceipt,
    ConnectionTestSummary,
)
from app.services import business_connections as connections
from app.services.business_connection_tests import BusinessConnectionTestService

TEST_URL = os.getenv("SCENARIOS_POSTGRES_TEST_URL")
pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="Explicit isolated PostgreSQL database required"
)


def migration(name):
    path = Path(__file__).parents[1] / "alembic/versions" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_test_migration_claim_race_and_cancel_fence_preserve_reports(monkeypatch):
    assert TEST_URL is not None
    url = make_url(TEST_URL)
    assert (url.database or "").startswith("tgo_scenarios_test_")
    assert url.host in {"127.0.0.1", "localhost"} and url.port == 15432
    monkeypatch.setattr(
        settings,
        "BUSINESS_CONNECTION_CREDENTIAL_KEY",
        SecretStr(base64.urlsafe_b64encode(os.urandom(32)).decode()),
    )
    schema = "connection_tests_" + uuid4().hex
    engine = create_engine(
        url.set(drivername="postgresql+psycopg2"),
        connect_args={"options": f"-csearch_path={schema}"},
    )
    owner_migration = migration("0048_business_connections.py")
    test_migration = migration("0049_business_connection_tests.py")
    try:
        with engine.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{schema}"'))
            db.execute(text(f'SET search_path TO "{schema}"'))
            Project.__table__.create(db)
            Staff.__table__.create(db)
            with Operations.context(MigrationContext.configure(db)):
                owner_migration.upgrade()
                test_migration.upgrade()
                test_migration.downgrade()
                test_migration.upgrade()
            assert inspect(db).has_table("api_business_connection_tests", schema=schema)
        project, actor = uuid4(), uuid4()
        with Session(engine) as db:
            db.add(Project(id=project, name="隔离报告企业", api_key=str(uuid4())))
            db.add(
                Staff(
                    id=actor,
                    project_id=project,
                    username="test-admin",
                    password_hash="not-a-password",
                    role="admin",
                )
            )
            db.commit()
            connection = connections.create(
                db,
                project,
                actor,
                BusinessConnectionCreate(
                    request_id=uuid4(),
                    name="隔离连接",
                    capabilities=["order_query"],
                    configuration={"base_url": "https://orders.example.test"},
                ),
            ).id
        payload = BusinessConnectionTestRequest(
            request_id=uuid4(),
            expected_revision=1,
            operation="order_query",
            order_no="ORDER-ISOLATED-001",
            customer_id="customer-isolated",
        )
        barrier = Barrier(2)

        def claim_once():
            with Session(engine) as db:
                barrier.wait(timeout=30)
                return BusinessConnectionTestService(db).claim(
                    project, actor, connection, payload
                )

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(claim_once) for _ in range(2)]
            claims = [future.result(timeout=45) for future in futures]
        assert claims[0].report.id == claims[1].report.id
        assert sum(claim.command is not None for claim in claims) == 1
        original = next(claim for claim in claims if claim.command is not None)
        receipt = ConnectionTestReceipt(
            scope="business_connection_test",
            can_enable=False,
            **original.command.model_dump(exclude={"order_no", "customer_id"}),
            mode="http",
            outcome="passed",
            summary=ConnectionTestSummary(status="已发货"),
            error_code=None,
            checked_at=datetime.now(UTC),
        )
        with Session(engine) as db:
            service = BusinessConnectionTestService(db)
            service.cancel(project, actor, connection, payload.request_id)
        with Session(engine) as db:
            late = BusinessConnectionTestService(db).finish(
                project,
                connection,
                original.report.id,
                original.attempt_id,
                receipt=receipt,
            )
            assert late.status == "cancelled"
            assert connections.get(db, project, connection).test_status == "not_tested"
            row = db.get(BusinessConnectionTest, original.report.id)
            assert payload.order_no not in str(row.__dict__)
            assert payload.customer_id not in str(row.__dict__)
        with engine.begin() as db:
            with pytest.raises(RuntimeError, match="preserve"):
                with Operations.context(MigrationContext.configure(db)):
                    test_migration.downgrade()
            assert (
                db.scalar(text("SELECT count(*) FROM api_business_connection_tests"))
                == 1
            )
    finally:
        with engine.begin() as db:
            db.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
