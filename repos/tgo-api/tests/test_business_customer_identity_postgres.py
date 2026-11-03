"""Migrations, separate transactions and nonce races on isolated PostgreSQL."""

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
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.business_customer_identity import (
    BusinessCustomerBinding,
)
from app.models.project import Project
from app.models.staff import Staff
from app.models.platform import Platform
from app.models.visitor import Visitor
from app.models.visitor_business_scope import VisitorBusinessScope
from app.schemas.business_connections import BusinessConnectionCreate
from app.schemas.business_customer_identity import (
    CustomerIdentityPolicyWrite,
    CustomerIdentitySession,
)
from app.services import business_connections
from app.services.business_customer_identity import BusinessCustomerIdentityService
from tests.test_business_customer_identity import sign, KEY, VIEWER_SECRET
from app.services.visitor_access_crypto import access_digest
from types import SimpleNamespace

URL = os.getenv("SCENARIOS_POSTGRES_TEST_URL")
pytestmark = pytest.mark.skipif(
    not URL, reason="Explicit isolated PostgreSQL database required"
)


def migration(name):
    path = Path(__file__).parents[1] / "alembic/versions" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_customer_identity_migration_concurrency_rotation_and_recovery(monkeypatch):
    assert URL is not None
    url = make_url(URL)
    assert (
        (url.database or "").startswith("tgo_scenarios_test_")
        and url.host in {"localhost", "127.0.0.1"}
        and url.port == 15432
    )
    monkeypatch.setattr(
        settings,
        "BUSINESS_CONNECTION_CREDENTIAL_KEY",
        SecretStr(base64.urlsafe_b64encode(os.urandom(32)).decode()),
    )
    schema = "customer_identity_" + uuid4().hex
    engine = create_engine(
        url.set(drivername="postgresql+psycopg2"),
        connect_args={"options": "-csearch_path=" + schema},
    )
    owner = migration("0048_business_connections.py")
    report = migration("0049_business_connection_tests.py")
    identity = migration("0050_business_customer_identity.py")
    try:
        with engine.begin() as db:
            db.execute(text('CREATE SCHEMA "' + schema + '"'))
            db.execute(text('SET search_path TO "' + schema + '"'))
            for model in (Project, Staff, Platform, Visitor):
                model.__table__.create(db)
            with Operations.context(MigrationContext.configure(db)):
                owner.upgrade()
                report.upgrade()
                identity.upgrade()
                identity.downgrade()
                identity.upgrade()
            VisitorBusinessScope.__table__.create(db)
            assert inspect(db).has_table(
                "api_business_customer_bindings", schema=schema
            )
        project, actor, platform, visitor = [uuid4() for _ in range(4)]
        with Session(engine) as db:
            db.add(
                Project(
                    id=project,
                    name="isolated-customer-policy",
                    api_key="fixture-only-project-key",
                )
            )
            db.commit()
            db.add(
                Staff(
                    id=actor,
                    project_id=project,
                    username="identity-fixture-admin",
                    password_hash="not-an-authenticating-hash",
                    role="admin",
                    account_enabled=True,
                )
            )
            db.add(
                Platform(
                    id=platform,
                    project_id=project,
                    type="website",
                    name="隔离渠道",
                    is_active=True,
                )
            )
            db.commit()
            db.add(
                Visitor(
                    id=visitor,
                    project_id=project,
                    platform_id=platform,
                    platform_open_id="untrusted-open-id",
                    access_secret_digest=access_digest(
                        SimpleNamespace(id=visitor), VIEWER_SECRET
                    ),
                )
            )
            db.commit()
            saved = business_connections.create(
                db,
                project,
                actor,
                BusinessConnectionCreate(
                    request_id=uuid4(),
                    name="隔离商户",
                    configuration={"base_url": "https://merchant.example.test"},
                ),
            )
            connection = saved.id
            policy = BusinessCustomerIdentityService(db).configure(
                project,
                actor,
                connection,
                CustomerIdentityPolicyWrite(
                    expected_revision=0,
                    platform_ids=[platform],
                    signing_secret=SecretStr(KEY),
                    enabled=True,
                ),
            )
        now = int(datetime.now(UTC).timestamp())
        assertion = sign(
            {
                "purpose": "business_customer_identity",
                "project_id": str(project),
                "connection_id": str(connection),
                "platform_id": str(platform),
                "visitor_id": str(visitor),
                "customer_id": "member-private",
                "nonce": str(uuid4()),
                "policy_revision": policy.revision,
                "issued_at": now,
                "expires_at": now + 300,
            }
        )
        barrier = Barrier(2)

        def exchange() -> CustomerIdentitySession:
            with Session(engine) as db:
                barrier.wait(timeout=15)
                return BusinessCustomerIdentityService(db).exchange(
                    connection, assertion, visitor_access_token=VIEWER_SECRET
                )

        with ThreadPoolExecutor(max_workers=2) as pool:
            left = pool.submit(exchange)
            right = pool.submit(exchange)
            first, second = left.result(timeout=30), right.result(timeout=30)
        assert (
            first.binding_id == second.binding_id
            and first.session_token == second.session_token
        )
        with Session(engine) as db:
            service = BusinessCustomerIdentityService(db)
            assert len(list(db.scalars(select(BusinessCustomerBinding)))) == 1
            assert (
                service.resolve(
                    project, connection, visitor, first.session_token
                ).customer_id.get_secret_value()
                == "member-private"
            )
            assert (
                service.exchange(connection, assertion).binding_id == first.binding_id
            )
            service.configure(
                project,
                actor,
                connection,
                CustomerIdentityPolicyWrite(
                    expected_revision=1, platform_ids=[platform], enabled=False
                ),
            )
            from app.core.exceptions import TGOAPIException

            with pytest.raises(TGOAPIException):
                service.resolve(project, connection, visitor, first.session_token)
        with engine.begin() as db:
            with Operations.context(MigrationContext.configure(db)):
                with pytest.raises(RuntimeError, match="preserve"):
                    identity.downgrade()
            assert inspect(db).has_table(
                "api_business_customer_identity_policies", schema=schema
            )
    finally:
        with engine.begin() as db:
            db.execute(text('DROP SCHEMA IF EXISTS "' + schema + '" CASCADE'))
        engine.dispose()
