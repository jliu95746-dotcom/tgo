"""Workflow audit gateway keeps tenant scope and rejects bad upstream data."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from app.api.v1.endpoints import ai_workflows
from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.core.security import get_current_active_user
from app.schemas.business_query_audit import BusinessQueryAuditList
from app.services.business_query_audit_client import (
    read_business_query_audits, read_business_message_audits,
)


@pytest.mark.asyncio
async def test_workflow_audit_gateway_uses_admin_tenant(monkeypatch):
    project, query_id = uuid4(), uuid4()
    read = AsyncMock(return_value=BusinessQueryAuditList(events=[], total=0))
    monkeypatch.setattr(ai_workflows, "read_business_query_audits", read)
    app = FastAPI()
    app.include_router(ai_workflows.router, prefix="/v1/ai/workflows")
    staff = SimpleNamespace(project_id=project, role="admin", username="admin")
    app.dependency_overrides[get_current_active_user] = lambda: staff
    path = f"/v1/ai/workflows/business-queries/{query_id}/audits"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.get(path, params={"project_id": str(uuid4())})
        staff.role = "agent"
        denied = await client.get(path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    read.assert_awaited_once_with(project, query_id)
    assert denied.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["foreign_project", "foreign_query", "private_field"],
)
async def test_workflow_audit_client_rejects_bad_upstream_event(
    monkeypatch, case,
):
    project, query_id = uuid4(), uuid4()
    monkeypatch.setattr(
        settings, "BUSINESS_CONNECTION_SERVICE_TOKEN",
        SecretStr("audit-read-service-token-at-least-32-characters"),
    )
    original_client = httpx.AsyncClient

    async def handler(request):
        assert request.url.path == f"/business/execution/audits/{query_id}"
        assert request.url.params["project_id"] == str(project)
        assert request.headers["X-Business-Service-Token"]
        event = {
            "id": str(uuid4()),
            "project_id": str(
                uuid4() if case == "foreign_project" else project
            ),
            "query_id": str(uuid4() if case == "foreign_query" else query_id),
            "agent_id": str(uuid4()),
            "operation": "order_query", "outcome": "success",
            "duration_ms": 4.0, "created_at": datetime.now(UTC).isoformat(),
        }
        if case == "private_field":
            event["parameter_fingerprint"] = "private-order"
        return httpx.Response(200, json={"total": 1, "events": [event]})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(
        **kwargs, transport=httpx.MockTransport(handler),
    ))
    with pytest.raises(TGOAPIException) as error:
        await read_business_query_audits(project, query_id)
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_message_audit_client_posts_private_scope_and_bounded_response(monkeypatch):
    project = uuid4()
    monkeypatch.setattr(settings, "BUSINESS_CONNECTION_SERVICE_TOKEN",
                        SecretStr("audit-read-service-token-at-least-32-characters"))
    original_client = httpx.AsyncClient

    async def handler(request):
        assert request.method == "POST"
        assert request.url.path == "/business/execution/audits/by-message"
        assert "source-1" not in str(request.url)
        assert request.headers["X-Business-Service-Token"]
        assert json.loads(request.content) == {
            "project_id": str(project), "source_message_id": "source-1",
            "response_client_msg_no": "reply-1", "skip": 2, "limit": 20,
        }
        return httpx.Response(200, json={"total": 0, "events": []})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(
        **kwargs, transport=httpx.MockTransport(handler),
    ))
    result = await read_business_message_audits(project, "source-1", "reply-1", 2, 20)
    assert result.total == 0 and result.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["foreign_project", "private_field", "over_limit"])
async def test_message_audit_client_rejects_unsafe_upstream_response(monkeypatch, case):
    project = uuid4()
    monkeypatch.setattr(settings, "BUSINESS_CONNECTION_SERVICE_TOKEN",
                        SecretStr("audit-read-service-token-at-least-32-characters"))
    original_client = httpx.AsyncClient

    async def handler(request):
        event = {
            "id": str(uuid4()),
            "project_id": str(uuid4() if case == "foreign_project" else project),
            "query_id": str(uuid4()), "agent_id": str(uuid4()),
            "operation": "order_query", "outcome": "success",
            "duration_ms": 4.0, "created_at": datetime.now(UTC).isoformat(),
        }
        if case == "private_field":
            event["customer_message"] = "private text"
        events = [event] * (21 if case == "over_limit" else 1)
        return httpx.Response(200, json={"total": len(events), "events": events})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(
        **kwargs, transport=httpx.MockTransport(handler),
    ))
    with pytest.raises(TGOAPIException) as error:
        await read_business_message_audits(project, "source-1", "reply-1", 0, 20)
    assert error.value.status_code == 503
