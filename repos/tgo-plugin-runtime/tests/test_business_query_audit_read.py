"""Read-only audit lookup stays within one tenant and exposes no raw inputs."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import httpx
from fastapi import FastAPI
from pydantic import SecretStr
from pydantic import ValidationError

from app.api.business_execution import router
from app.config import settings
from app.core.database import get_async_db
from app.domain.business_tools.models import message_lookup_digest
from app.domain.business_tools.models import BusinessQueryContextRequest
from app.models.business_query_audit import BusinessQueryAuditRecord
from app.schemas.business_query_audit import BusinessMessageAuditQuery
from app.services.business_query_audit_read import list_message_query_audits, list_query_audits


def test_audit_lookup_has_tenant_request_order_index():
    indexes = {
        tuple(column.name for column in index.columns)
        for index in BusinessQueryAuditRecord.__table__.indexes
    }
    assert ("tenant_id", "request_id", "created_at", "id") in indexes
    assert (
        "tenant_id", "source_message_digest", "response_message_digest",
        "created_at", "id",
    ) in indexes


def test_untrusted_query_transport_cannot_claim_a_formal_message_pair():
    with pytest.raises(ValidationError):
        BusinessQueryContextRequest.model_validate({
            "tenant_id": str(uuid4()), "visitor_id": "visitor",
            "conversation_id": "conversation", "request_id": "request",
            "actor_id": "actor", "source_message_id": "source",
            "response_client_msg_no": "response",
        })


@pytest.mark.asyncio
async def test_message_audit_lookup_uses_both_scoped_digests_and_no_raw_ids():
    project, query_id = uuid4(), uuid4()
    source_id, response_no = "source-private-1", "response-private-1"
    source_digest = message_lookup_digest(project, source_id)
    response_digest = message_lookup_digest(project, response_no)
    row = SimpleNamespace(
        id=uuid4(), tenant_id=project, request_id=str(query_id),
        actor_id=str(uuid4()), operation="order_query", outcome="success",
        duration_ms=8.0, created_at=datetime.now(UTC),
        source_message_digest=source_digest, response_message_digest=response_digest,
    )
    db = SimpleNamespace(
        scalar=AsyncMock(return_value=1),
        execute=AsyncMock(return_value=SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: [row]),
        )),
    )
    query = BusinessMessageAuditQuery(
        project_id=project, source_message_id=source_id,
        response_client_msg_no=response_no,
    )
    result = await list_message_query_audits(db, query)
    assert result.total == 1 and result.events[0].query_id == query_id
    assert source_id not in result.model_dump_json()
    assert response_no not in result.model_dump_json()
    statement = db.execute.await_args.args[0].compile()
    assert project in statement.params.values()
    assert source_digest in statement.params.values()
    assert response_digest in statement.params.values()
    assert source_id not in str(statement.params)
    assert response_no not in str(statement.params)


@pytest.mark.asyncio
async def test_message_audit_lookup_rejects_row_with_other_response():
    project = uuid4()
    query = BusinessMessageAuditQuery(
        project_id=project, source_message_id="source-1",
        response_client_msg_no="response-1",
    )
    row = SimpleNamespace(
        tenant_id=project,
        source_message_digest=message_lookup_digest(project, "source-1"),
        response_message_digest=message_lookup_digest(project, "response-2"),
    )
    db = SimpleNamespace(
        scalar=AsyncMock(return_value=1),
        execute=AsyncMock(return_value=SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: [row]),
        )),
    )
    with pytest.raises(ValueError, match="scope mismatch"):
        await list_message_query_audits(db, query)


@pytest.mark.asyncio
async def test_audit_lookup_filters_tenant_and_query_and_returns_safe_fields():
    project, query_id = uuid4(), uuid4()
    row = SimpleNamespace(
        id=uuid4(), tenant_id=project, request_id=str(query_id),
        actor_id=str(uuid4()), operation="order_query", outcome="success",
        duration_ms=23.5, created_at=datetime.now(UTC),
        visitor_fingerprint="secret-visitor",
        parameter_fingerprint="secret-order",
    )
    db = SimpleNamespace(
        scalar=AsyncMock(return_value=1),
        execute=AsyncMock(return_value=SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: [row]),
        )),
    )
    result = await list_query_audits(db, project, query_id)
    assert result.total == 1
    assert result.events[0].query_id == query_id
    assert result.events[0].project_id == project
    assert "fingerprint" not in result.model_dump_json()
    query = db.execute.await_args.args[0]
    compiled = query.compile()
    assert "pg_business_query_audit.tenant_id" in str(compiled)
    assert "pg_business_query_audit.request_id" in str(compiled)
    assert project in compiled.params.values()
    assert str(query_id) in compiled.params.values()


@pytest.mark.asyncio
async def test_audit_lookup_rejects_upstream_scope_mismatch():
    project, query_id = uuid4(), uuid4()
    foreign = SimpleNamespace(tenant_id=uuid4(), request_id=str(query_id))
    db = SimpleNamespace(
        scalar=AsyncMock(return_value=1),
        execute=AsyncMock(return_value=SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: [foreign]),
        )),
    )
    with pytest.raises(ValueError):
        await list_query_audits(db, project, query_id)


@pytest.mark.asyncio
async def test_private_audit_route_requires_token_and_omits_fingerprints(
    monkeypatch,
):
    project, query_id = uuid4(), uuid4()
    monkeypatch.setattr(
        settings, "BUSINESS_CONNECTION_SERVICE_TOKEN",
        SecretStr("audit-read-service-token-at-least-32-characters"),
    )
    row = SimpleNamespace(
        id=uuid4(), tenant_id=project, request_id=str(query_id),
        actor_id=str(uuid4()), operation="logistics_query", outcome="denied",
        duration_ms=10.0, created_at=datetime.now(UTC),
        visitor_fingerprint="private-customer",
        parameter_fingerprint="private-order",
    )
    db = SimpleNamespace(
        scalar=AsyncMock(return_value=1),
        execute=AsyncMock(return_value=SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: [row]),
        )),
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_async_db] = lambda: db
    path = f"/business/execution/audits/{query_id}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        denied = await client.get(path, params={"project_id": str(project)})
        allowed = await client.get(
            path, params={"project_id": str(project)},
            headers={
                "X-Business-Service-Token":
                    "audit-read-service-token-at-least-32-characters",
            },
        )
    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert allowed.headers["cache-control"] == "no-store"
    assert allowed.json()["events"][0]["outcome"] == "denied"
    assert "private" not in allowed.text


@pytest.mark.asyncio
async def test_private_message_audit_route_requires_token_and_omits_message_ids(
    monkeypatch,
):
    project, query_id = uuid4(), uuid4()
    source_id, response_no = "private-source-1", "private-response-1"
    monkeypatch.setattr(
        settings, "BUSINESS_CONNECTION_SERVICE_TOKEN",
        SecretStr("audit-read-service-token-at-least-32-characters"),
    )
    row = SimpleNamespace(
        id=uuid4(), tenant_id=project, request_id=str(query_id),
        actor_id=str(uuid4()), operation="membership_query", outcome="timeout",
        duration_ms=12.0, created_at=datetime.now(UTC),
        source_message_digest=message_lookup_digest(project, source_id),
        response_message_digest=message_lookup_digest(project, response_no),
    )
    db = SimpleNamespace(
        scalar=AsyncMock(return_value=1),
        execute=AsyncMock(return_value=SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: [row]),
        )),
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_async_db] = lambda: db
    payload = {
        "project_id": str(project), "source_message_id": source_id,
        "response_client_msg_no": response_no,
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        denied = await client.post("/business/execution/audits/by-message", json=payload)
        allowed = await client.post(
            "/business/execution/audits/by-message", json=payload,
            headers={"X-Business-Service-Token":
                     "audit-read-service-token-at-least-32-characters"},
        )
    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert allowed.headers["cache-control"] == "no-store"
    assert allowed.json()["events"][0]["query_id"] == str(query_id)
    assert source_id not in allowed.text and response_no not in allowed.text
