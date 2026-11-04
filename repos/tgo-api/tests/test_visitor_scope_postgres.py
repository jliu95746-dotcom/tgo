"""An owned isolated database exercises real upgrades and competing transactions."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import os
from threading import Barrier
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import SecretStr
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.models.business_customer_identity import BusinessCustomerBinding
from app.models.project import Project
from app.models.platform import Platform
from app.models.staff import Staff
from app.models.visitor import Visitor
from app.models.visitor_business_scope import VisitorBusinessScope
from app.models.visitor_session import VisitorSession
from app.models.ai_interaction_run import AIInteractionRun
from app.models.visitor_waiting_queue import VisitorWaitingQueue
from app.schemas.business_connections import BusinessConnectionCreate
from app.schemas.business_customer_identity import CustomerIdentityPolicyWrite
from app.schemas.visitor_access import VisitorAccessInput
from app.services import business_connections
from app.services.business_customer_identity import BusinessCustomerIdentityService
from app.services.visitor_access import end_access, registration_access, require_access
from tests.test_business_customer_identity import sign, KEY
from tests.test_business_customer_identity_postgres import migration


URL = os.getenv("SCENARIOS_POSTGRES_TEST_URL")
pytestmark = pytest.mark.skipif(not URL, reason="Explicit isolated PostgreSQL required")


@pytest.fixture
def owned_database():
    assert URL is not None
    url = make_url(URL)
    assert (url.database or "").startswith("tgo_scenarios_test_")
    assert url.host in {"127.0.0.1", "localhost"} and url.port == 15432
    schema = "visitor_scope_" + uuid4().hex
    engine = create_engine(
        url.set(drivername="postgresql+psycopg2"),
        connect_args={"options": "-csearch_path=" + schema},
    )
    scope_migration = migration("0051_visitor_business_scope.py")
    try:
        with engine.begin() as db:
            db.execute(text('CREATE SCHEMA "' + schema + '"'))
            db.execute(text('SET search_path TO "' + schema + '"'))
            for model in (Project, Staff, Platform, Visitor, VisitorSession, AIInteractionRun, VisitorWaitingQueue):
                model.__table__.create(db)
            # Reconstruct the previous Visitor schema before executing the real migration.
            for field in (
                "access_retired_at",
                "access_revision",
                "access_secret_digest",
            ):
                db.execute(text('ALTER TABLE api_visitors DROP COLUMN "' + field + '"'))
            with Operations.context(MigrationContext.configure(db)):
                migration("0048_business_connections.py").upgrade()
                migration("0049_business_connection_tests.py").upgrade()
                migration("0050_business_customer_identity.py").upgrade()
                scope_migration.upgrade()
                scope_migration.downgrade()
                scope_migration.upgrade()
            assert inspect(db).has_table("api_visitor_business_scopes", schema=schema)
        project, actor, platform, visitor_id = [uuid4() for _ in range(4)]
        with Session(engine) as db:
            db.add(
                Project(
                    id=project,
                    name="visitor-scope-fixture",
                    api_key="isolated-fixture-only",
                )
            )
            db.commit()
            db.add(
                Staff(
                    id=actor,
                    project_id=project,
                    username="visitor-scope-admin",
                    password_hash="unused",
                    role="admin",
                    account_enabled=True,
                )
            )
            db.add(
                Platform(
                    id=platform,
                    project_id=project,
                    type="website",
                    name="访问隔离",
                    is_active=True,
                )
            )
            db.commit()
            visitor = Visitor(
                id=visitor_id,
                project_id=project,
                platform_id=platform,
                platform_open_id="not-an-access-credential",
            )
            db.add(visitor)
            db.commit()
            owner_secret, _ = registration_access(
                db, visitor, VisitorAccessInput(), new=True
            )
            db.commit()
            connection = business_connections.create(
                db,
                project,
                actor,
                BusinessConnectionCreate(
                    request_id=uuid4(),
                    name="隔离只读商户",
                    configuration={"base_url": "https://merchant.example.test"},
                ),
            ).id
            BusinessCustomerIdentityService(db).configure(
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
        yield (
            engine,
            scope_migration,
            project,
            connection,
            platform,
            visitor_id,
            owner_secret,
        )
    finally:
        with engine.begin() as db:
            db.execute(text('DROP SCHEMA IF EXISTS "' + schema + '" CASCADE'))
        engine.dispose()


def assertion(project, connection, platform, visitor, customer):
    now = int(datetime.now(UTC).timestamp())
    return sign(
        {
            "purpose": "business_customer_identity",
            "project_id": str(project),
            "connection_id": str(connection),
            "platform_id": str(platform),
            "visitor_id": str(visitor),
            "customer_id": customer,
            "nonce": str(uuid4()),
            "policy_revision": 1,
            "issued_at": now,
            "expires_at": now + 300,
        }
    )


def test_real_customer_race_and_restart_preserve_one_owner(owned_database):
    engine, _, project, connection, platform, visitor, owner_secret = owned_database
    barrier = Barrier(2)

    def exchange(customer):
        with Session(engine) as db:
            barrier.wait(timeout=15)
            try:
                return BusinessCustomerIdentityService(db).exchange(
                    connection,
                    assertion(project, connection, platform, visitor, customer),
                    visitor_access_token=owner_secret,
                )
            except TGOAPIException:
                db.rollback()
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        left, right = [
            pool.submit(exchange, customer) for customer in ("member-A", "member-B")
        ]
        results = [left.result(timeout=30), right.result(timeout=30)]
    assert sum(item is not None for item in results) == 1
    first = next(item for item in results if item is not None)
    engine.dispose()
    with Session(engine) as db:
        assert len(list(db.scalars(select(VisitorBusinessScope)))) == 1
        assert len(list(db.scalars(select(BusinessCustomerBinding)))) == 1
        resolved = BusinessCustomerIdentityService(db).resolve(
            project, connection, visitor, first.session_token
        )
        assert resolved.customer_id.get_secret_value() in {"member-A", "member-B"}


def test_ending_access_cancels_owner_without_destructive_downgrade(owned_database):
    engine, scope_migration, project, connection, platform, visitor_id, secret = (
        owned_database
    )
    with Session(engine) as db:
        visitor = db.get(Visitor, visitor_id)
        receipt = BusinessCustomerIdentityService(db).exchange(
            connection,
            assertion(project, connection, platform, visitor_id, "member-A"),
            visitor_access_token=secret,
        )
        end_access(db, visitor, VisitorAccessInput(visitor_access_token=secret))
        ended_revision = visitor.access_revision
        end_access(db, visitor, VisitorAccessInput(visitor_access_token=secret))
        assert visitor.access_revision == ended_revision
    engine.dispose()
    with Session(engine) as db:
        visitor = db.get(Visitor, visitor_id)
        with pytest.raises(TGOAPIException):
            require_access(db, visitor, VisitorAccessInput(visitor_access_token=secret))
        with pytest.raises(TGOAPIException):
            BusinessCustomerIdentityService(db).resolve(
                project, connection, visitor_id, receipt.session_token
            )
        assert all(
            row.retired_at is not None
            for row in db.scalars(select(VisitorBusinessScope))
        )
    with engine.begin() as db:
        with Operations.context(MigrationContext.configure(db)):
            with pytest.raises(RuntimeError, match="保护记录"):
                scope_migration.downgrade()
        assert inspect(db).has_table("api_visitor_business_scopes")
