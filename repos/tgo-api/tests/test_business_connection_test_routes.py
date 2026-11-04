"""Public tests use current tenant/admin identity and recover original requests."""

import base64
import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints.business_connections import (
    router,
    get_connection_test_service,
)
from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import (
    TGOAPIException,
    tgo_api_exception_handler,
    validation_exception_handler,
)
from app.core.security import get_current_active_user
from app.models.project import Project
from app.models.staff import Staff
from app.models.business_connection import BusinessConnection
from app.models.business_connection_test import BusinessConnectionTest
from app.schemas.business_connections import BusinessConnectionCreate
from app.schemas.connection_test_protocol import (
    ConnectionTestReceipt,
    ConnectionTestSummary,
)
from app.services.business_connections import create
from app.services.business_connection_tests import BusinessConnectionTestService


@pytest.fixture
def fixture(monkeypatch):
    monkeypatch.setattr(settings, "SAAS_ENABLED", False)
    monkeypatch.setattr(
        settings,
        "BUSINESS_CONNECTION_CREDENTIAL_KEY",
        SecretStr(base64.urlsafe_b64encode(os.urandom(32)).decode()),
    )
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
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
        db.add_all([project, actor])
        db.commit()
        connection = create(
            db,
            project.id,
            actor.id,
            BusinessConnectionCreate(request_id=uuid4(), name="示例连接", mode="demo"),
        )
        adapter = AsyncMock()

        async def execute(command):
            return ConnectionTestReceipt(
                scope="business_connection_test",
                can_enable=False,
                **command.model_dump(exclude={"order_no", "customer_id"}),
                mode="demo",
                outcome="demo",
                summary=ConnectionTestSummary(status="已发货"),
                error_code=None,
                checked_at=datetime.now(UTC),
            )

        adapter.test_business_connection.side_effect = execute
        service, app = BusinessConnectionTestService(db, adapter=adapter), FastAPI()
        app.include_router(router, prefix="/v1/business-connections")
        app.add_exception_handler(TGOAPIException, tgo_api_exception_handler)
        app.add_exception_handler(RequestValidationError, validation_exception_handler)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_active_user] = lambda: actor
        app.dependency_overrides[get_connection_test_service] = lambda: service
        yield app, db, actor, connection, service, adapter
    engine.dispose()


def payload():
    return {
        "request_id": str(uuid4()),
        "expected_revision": 1,
        "operation": "order_query",
        "order_no": "ORDER-TEST-001",
        "customer_id": "customer-test",
    }


@pytest.mark.asyncio
async def test_admin_can_submit_recover_and_list_bounded_demo_report(fixture):
    app, db, actor, connection, service, adapter = fixture
    path, body = f"/v1/business-connections/{connection.id}/tests", payload()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.post(path, json=body)
        assert first.status_code == 200 and first.json()["outcome"] == "demo"
        assert (
            first.json()["can_enable"] is False and body["order_no"] not in first.text
        )
        replay = await client.post(path, json=body)
        assert replay.json()["id"] == first.json()["id"]
        saved = await client.get(path + "/" + body["request_id"])
        assert saved.json()["id"] == first.json()["id"]
        assert (await client.get(path)).json()[0]["id"] == first.json()["id"]
        assert adapter.test_business_connection.await_count == 1


@pytest.mark.asyncio
async def test_cancel_and_unknown_request_have_explicit_status(fixture):
    app, db, actor, connection, service, adapter = fixture
    from app.schemas.business_connection_tests import BusinessConnectionTestRequest

    body = payload()
    service.claim(
        actor.project_id,
        actor.id,
        connection.id,
        BusinessConnectionTestRequest.model_validate(body),
    )
    path = f"/v1/business-connections/{connection.id}/tests/{body['request_id']}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        cancelled = await client.post(path + "/cancel")
        assert (
            cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
        )
        assert (
            await client.get(
                f"/v1/business-connections/{connection.id}/tests/{uuid4()}"
            )
        ).status_code == 404


@pytest.mark.asyncio
async def test_non_admin_and_extra_identity_or_credentials_are_rejected(fixture):
    app, db, actor, connection, service, adapter = fixture
    path = f"/v1/business-connections/{connection.id}/tests"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        rejected = await client.post(
            path,
            json=payload()
            | {"project_id": str(uuid4()), "credentials": "do-not-echo-fixture-secret"},
        )
        assert rejected.status_code == 422 and "do-not-echo" not in rejected.text
        actor.role = "agent"
        db.commit()
        assert (await client.post(path, json=payload())).status_code == 403
        assert (await client.get(path)).status_code == 403
        adapter.test_business_connection.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_result_can_be_recovered_without_resending_credentials(fixture):
    app, db, actor, connection, service, adapter = fixture
    row = db.get(BusinessConnection, connection.id)
    path = f"/v1/business-connections/requests/{row.request_id}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        recovered = await client.get(path)
        assert recovered.status_code == 200 and recovered.json()["id"] == str(
            connection.id
        )
        assert (
            "credentials" not in recovered.json()
            and "request_digest" not in recovered.json()
        )
        assert (
            await client.get(f"/v1/business-connections/requests/{uuid4()}")
        ).status_code == 404
        actor.project_id = uuid4()
        db.commit()
        assert (await client.get(path)).status_code == 404
