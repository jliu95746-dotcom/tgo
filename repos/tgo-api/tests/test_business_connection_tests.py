"""Durable tests reject stale results and derive proof per current operation."""

import base64
import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import ConflictError, NotFoundError
from app.models.project import Project
from app.models.staff import Staff
from app.models.business_connection import BusinessConnection
from app.models.business_connection_test import BusinessConnectionTest
from app.schemas.business_connections import (
    BusinessConnectionCreate,
    BusinessConnectionUpdate,
)
from app.schemas.business_connection_tests import BusinessConnectionTestRequest
from app.schemas.connection_test_protocol import (
    ConnectionTestReceipt,
    ConnectionTestSummary,
)
from app.services import business_connections as connections
from app.services.business_connection_tests import BusinessConnectionTestService


@pytest.fixture
def fixture(monkeypatch):
    monkeypatch.setattr(
        settings,
        "BUSINESS_CONNECTION_CREDENTIAL_KEY",
        SecretStr(base64.urlsafe_b64encode(os.urandom(32)).decode()),
    )
    engine = create_engine("sqlite:///:memory:")
    for table in [
        Project.__table__,
        Staff.__table__,
        BusinessConnection.__table__,
        BusinessConnectionTest.__table__,
    ]:
        table.create(engine)
    with Session(engine) as db:
        project = Project(id=uuid4(), name="测试企业", api_key=str(uuid4()))
        actor = Staff(
            id=uuid4(),
            project_id=project.id,
            username="test-admin",
            password_hash="not-a-password",
            role="admin",
        )
        other = Staff(
            id=uuid4(),
            project_id=project.id,
            username="test-admin-2",
            password_hash="not-a-password",
            role="admin",
        )
        db.add_all([project, actor, other])
        db.commit()
        saved = connections.create(
            db,
            project.id,
            actor.id,
            BusinessConnectionCreate(
                request_id=uuid4(),
                name="测试连接",
                configuration={"base_url": "https://orders.example.test"},
            ),
        )
        yield db, project.id, actor.id, other.id, saved.id
    engine.dispose()


def request(operation="order_query", **changes):
    return BusinessConnectionTestRequest(
        request_id=uuid4(),
        expected_revision=1,
        operation=operation,
        order_no="ORDER-TEST-001",
        customer_id="customer-test",
    ).model_copy(update=changes)


class Adapter:
    def __init__(self):
        self.calls = []
        self.outcome = "passed"
        self.mode = "http"
        self.before_return = None

    async def test_business_connection(self, command):
        self.calls.append(command)
        if self.before_return:
            self.before_return(command)
        return ConnectionTestReceipt(
            scope="business_connection_test",
            can_enable=False,
            **command.model_dump(exclude={"order_no", "customer_id"}),
            mode=self.mode,
            outcome=self.outcome,
            summary=None
            if self.outcome == "failed"
            else ConnectionTestSummary(status="已发货"),
            error_code="denied" if self.outcome == "failed" else None,
            checked_at=datetime.now(UTC),
        )


def owner_metadata(db, project, actor, connection):
    from app.schemas.agent_business_connections import ConnectionOwnerQuery
    from app.services.agent_business_connection_owner import current_business_connection
    return current_business_connection(db, ConnectionOwnerQuery(
        project_id=project, requested_by=actor, connection_id=connection))


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["customer_profile_query", "membership_query"])
async def test_customer_query_report_is_saved_but_not_exposed_as_employee_operation(
    fixture, operation
):
    db, project, actor, _other, connection = fixture
    changed = connections.update(
        db, project, actor, connection,
        BusinessConnectionUpdate(
            expected_revision=1,
            capabilities=[operation],
            configuration={
                "base_url": "https://orders.example.test",
                "customer_profile_path": "/v1/customers/profile/query",
                "membership_path": "/v1/memberships/query",
            },
        ),
    )
    payload = BusinessConnectionTestRequest(
        request_id=uuid4(), expected_revision=changed.revision,
        operation=operation, customer_id="customer-test",
    )
    service = BusinessConnectionTestService(db, adapter=Adapter())
    report = await service.run(project, actor, connection, payload)
    assert report.status == "completed" and report.outcome == "passed"
    assert report.applicability.status == "current"
    assert payload.customer_id not in str(db.get(BusinessConnectionTest, report.id).receipt)
    owner = owner_metadata(db, project, actor, connection)
    assert owner.operations == [] and owner.configuration_tests == []
    assert owner.can_enable is False


def test_owner_operation_metadata_initially_has_no_test_or_execution_claim(fixture):
    db, project, actor, other, connection = fixture
    metadata = owner_metadata(db, project, actor, connection)
    assert {item.operation for item in metadata.configuration_tests} == {"order_query", "logistics_query"}
    assert all(item.status == "not_tested" and item.report_id is None and item.valid_until is None
               and item.can_enable is False for item in metadata.configuration_tests)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [None, "configuration", "expired", "reviewer", "new_failure", "expired_running"])
async def test_owner_operation_metadata_uses_current_individual_report(fixture, change):
    db, project, actor, other, connection = fixture
    report = await BusinessConnectionTestService(db, adapter=Adapter()).run(project, actor, connection, request())
    if change == "configuration":
        db.get(BusinessConnection, connection).configuration_revision += 1
    elif change == "expired":
        row = db.get(BusinessConnectionTest, report.id)
        row.created_at = datetime.now(UTC) - timedelta(days=3)
        row.completed_at = datetime.now(UTC) - timedelta(days=2)
        row.receipt = {**row.receipt, "checked_at": row.completed_at.isoformat()}
    elif change == "reviewer":
        db.get(Staff, actor).role = "agent"
    elif change == "new_failure":
        adapter = Adapter(); adapter.outcome = "failed"
        report = await BusinessConnectionTestService(db, adapter=adapter).run(project, actor, connection, request())
    elif change == "expired_running":
        row = db.get(BusinessConnectionTest, report.id)
        row.status = "running"; row.receipt = None; row.completed_at = None
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    metadata = owner_metadata(db, project, other, connection)
    order = next(item for item in metadata.configuration_tests if item.operation == "order_query")
    assert order.report_id == report.id and order.can_enable is False
    assert order.scope == "business_connection_configuration"
    if change is None:
        assert order.status == "current" and order.valid_until > datetime.now(UTC)
    else:
        assert order.status != "current"
    if change == "expired_running":
        assert order.status == "unavailable" and "evidence_unknown" in order.issues
    assert next(item for item in metadata.configuration_tests if item.operation == "logistics_query").status == "not_tested"
    assert not {"summary", "customer_id", "order_no", "credentials", "receipt"} & order.model_dump().keys()


@pytest.mark.asyncio
async def test_report_is_durable_idempotent_and_does_not_persist_private_inputs(
    fixture,
):
    db, project, actor, other, connection = fixture
    adapter, payload = Adapter(), request()
    service = BusinessConnectionTestService(db, adapter=adapter)
    result = await service.run(project, actor, connection, payload)
    db.expire_all()
    replay = await service.run(project, actor, connection, payload)
    assert replay.id == result.id and len(adapter.calls) == 1
    assert (
        result.status == "completed"
        and result.outcome == "passed"
        and not result.can_enable
    )
    row = db.get(BusinessConnectionTest, result.id)
    assert payload.order_no not in str(row.receipt) + str(row.__dict__)
    assert payload.customer_id not in str(row.receipt) + str(row.__dict__)
    assert service.get(project, other, connection, payload.request_id).id == result.id
    with pytest.raises(ConflictError):
        await service.run(
            project,
            actor,
            connection,
            payload.model_copy(update={"order_no": "ORDER-OTHER"}),
        )


@pytest.mark.asyncio
async def test_order_and_logistics_each_need_current_proof_and_names_keep_it(fixture):
    db, project, actor, other, connection = fixture
    service = BusinessConnectionTestService(db, adapter=Adapter())
    await service.run(project, actor, connection, request())
    assert connections.get(db, project, connection).test_status == "not_tested"
    await service.run(project, other, connection, request("logistics_query"))
    assert connections.get(db, project, connection).test_status == "passed"
    named = connections.update(
        db,
        project,
        actor,
        connection,
        BusinessConnectionUpdate(expected_revision=1, name="改名"),
    )
    assert named.test_status == "passed"
    enabled = connections.update(
        db,
        project,
        actor,
        connection,
        BusinessConnectionUpdate(expected_revision=2, enabled=True),
    )
    assert enabled.test_status == "passed" and enabled.enabled
    admin = db.get(Staff, actor)
    admin.role = "agent"
    db.commit()
    assert connections.get(db, project, connection).test_status == "not_tested"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["rename", "disable", "configuration", "role"])
async def test_authority_mutation_while_querying_never_creates_current_proof(
    fixture, change
):
    db, project, actor, other, connection = fixture
    adapter = Adapter()

    def mutate(command):
        if change == "role":
            db.get(Staff, actor).account_enabled = False
            db.commit()
        else:
            patches = {
                "rename": {"name": "新名称"},
                "disable": {"enabled": False},
                "configuration": {
                    "configuration": {"base_url": "https://other.example.test"}
                },
            }
            if change == "disable":
                row = db.get(BusinessConnection, connection)
                row.authorization_revision += 1
                db.commit()
            else:
                connections.update(
                    db,
                    project,
                    actor,
                    connection,
                    BusinessConnectionUpdate(expected_revision=1, **patches[change]),
                )

    adapter.before_return = mutate
    result = await BusinessConnectionTestService(db, adapter=adapter).run(
        project, actor, connection, request()
    )
    assert (
        result.status == "failed"
        and result.error_code == "authority_changed"
        and result.summary is None
    )
    assert connections.get(db, project, connection).test_status == "not_tested"


@pytest.mark.asyncio
async def test_latest_failed_test_invalidates_old_pass(fixture):
    db, project, actor, other, connection = fixture
    adapter = Adapter()
    service = BusinessConnectionTestService(db, adapter=adapter)
    await service.run(project, actor, connection, request())
    await service.run(project, actor, connection, request("logistics_query"))
    assert connections.get(db, project, connection).test_status == "passed"
    adapter.outcome = "failed"
    failed = await service.run(project, actor, connection, request())
    assert (
        failed.outcome == "failed"
        and connections.get(db, project, connection).test_status == "not_tested"
    )


@pytest.mark.asyncio
async def test_expired_report_and_forged_cached_fields_are_not_proof(fixture):
    db, project, actor, other, connection = fixture
    service = BusinessConnectionTestService(db, adapter=Adapter())
    report = await service.run(project, actor, connection, request())
    await service.run(project, actor, connection, request("logistics_query"))
    old = db.get(BusinessConnectionTest, report.id)
    old.completed_at = datetime.now(UTC) - timedelta(days=3)
    old.receipt = old.receipt | {"checked_at": old.completed_at.isoformat()}
    db.commit()
    assert connections.get(db, project, connection).test_status == "not_tested"


@pytest.mark.asyncio
async def test_demo_never_enables_real_connection(fixture):
    db, project, actor, other, connection = fixture
    changed = connections.update(
        db,
        project,
        actor,
        connection,
        BusinessConnectionUpdate(expected_revision=1, mode="demo"),
    )
    adapter = Adapter()
    adapter.mode, adapter.outcome = "demo", "demo"
    result = await BusinessConnectionTestService(db, adapter=adapter).run(
        project, actor, connection, request(expected_revision=changed.revision)
    )
    assert result.outcome == "demo" and result.can_enable is False
    assert connections.get(db, project, connection).test_status == "not_tested"


@pytest.mark.asyncio
async def test_cancelled_or_expired_attempt_cannot_receive_late_success(fixture):
    db, project, actor, other, connection = fixture
    service = BusinessConnectionTestService(db, adapter=Adapter())
    payload = request()
    claim = service.claim(project, actor, connection, payload)
    running = await service.run(project, actor, connection, payload)
    assert running.status == "running" and not service.adapter.calls
    cancelled = service.cancel(project, actor, connection, payload.request_id)
    receipt = await service.adapter.test_business_connection(claim.command)
    late = service.finish(
        project, connection, claim.report.id, claim.attempt_id, receipt=receipt
    )
    assert cancelled.status == late.status == "cancelled"
    expired = service.claim(project, actor, connection, request())
    row = db.get(BusinessConnectionTest, expired.report.id)
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    result = service.get(project, actor, connection, row.request_id)
    assert result.status == "failed" and result.error_code == "timeout"


@pytest.mark.asyncio
async def test_transport_failure_persists_safe_terminal_and_does_not_repeat(fixture):
    db, project, actor, other, connection = fixture
    adapter = Adapter()
    adapter.before_return = lambda command: (_ for _ in ()).throw(
        RuntimeError("private-fixture-error")
    )
    service, payload = BusinessConnectionTestService(db, adapter=adapter), request()
    report = await service.run(project, actor, connection, payload)
    assert (
        report.status == "failed"
        and "private-fixture-error" not in report.model_dump_json()
    )
    assert (
        await service.run(project, actor, connection, payload)
    ).id == report.id and len(adapter.calls) == 1


def test_other_project_and_revoked_admin_cannot_read_or_cancel(fixture):
    db, project, actor, other, connection = fixture
    service = BusinessConnectionTestService(db, adapter=Adapter())
    claimed = service.claim(project, actor, connection, request())
    from fastapi import HTTPException

    with pytest.raises((HTTPException, NotFoundError)):
        service.get(uuid4(), actor, connection, claimed.report.request_id)
    db.get(Staff, actor).role = "agent"
    db.commit()
    with pytest.raises(HTTPException):
        service.cancel(project, actor, connection, claimed.report.request_id)


@pytest.mark.asyncio
async def test_cancelled_caller_persists_terminal_and_original_retry_does_not_query_again(
    fixture,
):
    db, project, actor, other, connection = fixture
    from unittest.mock import AsyncMock

    adapter = AsyncMock()
    adapter.test_business_connection.side_effect = asyncio.CancelledError
    service, payload = BusinessConnectionTestService(db, adapter=adapter), request()
    with pytest.raises(asyncio.CancelledError):
        await service.run(project, actor, connection, payload)
    saved = service.get(project, actor, connection, payload.request_id)
    assert saved.status == "cancelled" and saved.summary is None
    assert (await service.run(project, actor, connection, payload)).id == saved.id
    adapter.test_business_connection.assert_awaited_once()


@pytest.mark.asyncio
async def test_report_exposes_current_validity_without_changing_historical_result(
    fixture,
):
    db, project, actor, other, connection = fixture
    service = BusinessConnectionTestService(db, adapter=Adapter())
    first = await service.run(project, actor, connection, request())
    assert first.applicability.status == "current"
    assert first.applicability.valid_until > first.completed_at
    named = connections.update(
        db,
        project,
        actor,
        connection,
        BusinessConnectionUpdate(expected_revision=1, name="新名称"),
    )
    assert (
        service.get(project, actor, connection, first.request_id).applicability.status
        == "current"
    )
    second = await service.run(
        project, actor, connection, request(expected_revision=named.revision)
    )
    history = service.get(project, actor, connection, first.request_id)
    assert history.outcome == "passed" and history.summary == first.summary
    assert (
        history.applicability.status == "stale"
        and "superseded" in history.applicability.issues
    )
    assert (
        service.get(project, actor, connection, second.request_id).applicability.status
        == "current"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change,reason",
    [
        ("configuration", "configuration_changed"),
        ("expired", "expired"),
        ("role", "reviewer_unavailable"),
        ("credential", "credential_unavailable"),
    ],
)
async def test_report_current_applicability_changes_with_owner_state(
    fixture, change, reason
):
    db, project, actor, other, connection = fixture
    service = BusinessConnectionTestService(db, adapter=Adapter())
    report = await service.run(project, actor, connection, request())
    if change == "configuration":
        connections.update(
            db,
            project,
            actor,
            connection,
            BusinessConnectionUpdate(
                expected_revision=1,
                configuration={"base_url": "https://other.example.test"},
            ),
        )
    elif change == "expired":
        row = db.get(BusinessConnectionTest, report.id)
        row.completed_at = datetime.now(UTC) - timedelta(days=3)
        # Expiry uses a historically coherent request, not a result before creation.
        row.created_at = row.completed_at - timedelta(seconds=1)
        row.receipt = row.receipt | {"checked_at": row.completed_at.isoformat()}
        db.commit()
    elif change == "role":
        db.get(Staff, actor).account_enabled = False
        db.commit()
    else:
        db.get(
            BusinessConnection, connection
        ).credential_ciphertext = "invalid-fixture-ciphertext"
        db.commit()
    current = service.get(project, other, connection, report.request_id)
    assert current.outcome == "passed"
    assert (
        current.applicability.status in {"stale", "unavailable"}
        and reason in current.applicability.issues
    )


@pytest.mark.asyncio
async def test_demo_report_never_claims_current_real_proof(fixture):
    db, project, actor, other, connection = fixture
    demo = connections.update(
        db,
        project,
        actor,
        connection,
        BusinessConnectionUpdate(expected_revision=1, mode="demo"),
    )
    adapter = Adapter()
    adapter.mode, adapter.outcome = "demo", "demo"
    result = await BusinessConnectionTestService(db, adapter=adapter).run(
        project, actor, connection, request(expected_revision=demo.revision)
    )
    assert result.applicability.status == "not_passed"
    assert (
        "demo_only" in result.applicability.issues
        and result.applicability.valid_until is None
    )
