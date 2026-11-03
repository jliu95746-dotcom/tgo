"""Public workflow capabilities are authenticated, tenant scoped, and secret free."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.endpoints.ai_workflows import router
from app.core.security import get_current_active_user
from app.schemas.ai_workflows import WorkflowExecution, WorkflowExecuteRequest
from app.schemas.workflow_capabilities import WorkflowCapabilities
from app.services.workflow_client import workflow_client


@pytest.mark.asyncio
async def test_capabilities_derive_tenant_from_authenticated_identity(monkeypatch):
    app = FastAPI()
    app.include_router(router, prefix="/v1/ai/workflows")
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(project_id="owned-project")
    result = WorkflowCapabilities(nodes=[{"type": "tool", "available": True}], execution_modes=["draft_test", "production"])
    get = AsyncMock(return_value=result)
    monkeypatch.setattr(workflow_client, "get_capabilities", get)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/ai/workflows/capabilities?project_id=foreign-project")
    assert response.status_code == 200
    get.assert_awaited_once_with("owned-project")
    assert response.json()["nodes"][0]["available"] is True


def test_execution_contract_keeps_version_and_mode_but_excludes_definition():
    record = WorkflowExecution.model_validate({
        "id": "owned-run", "workflow_id": "owned-workflow", "status": "pending",
        "started_at": "2026-10-11T00:00:00Z", "workflow_version": 3,
        "employee_agent_id": "a471a04d-68b9-4574-a2da-2b1776168ed8",
        "release_id": "release-a", "definition_digest": "a" * 64,
        "ai_request_id": "ai-run-a", "business_request_id": "business-a",
        "visitor_session_id": "session-a",
        "source_message_id": "message-a", "source_channel": "web",
        "execution_mode": "draft_test", "definition_snapshot": {"secret": "private-fixture"},
    })
    assert record.workflow_version == 3
    assert record.execution_mode == "draft_test"
    assert record.employee_agent_id == "a471a04d-68b9-4574-a2da-2b1776168ed8"
    assert record.definition_digest == "a" * 64
    assert record.ai_request_id == "ai-run-a"
    assert record.business_request_id == "business-a"
    assert record.visitor_session_id == "session-a"
    assert record.source_message_id == "message-a"
    assert record.source_channel == "web"
    assert "private-fixture" not in record.model_dump_json()
    assert WorkflowExecuteRequest(execution_mode="production").model_dump()["execution_mode"] == "production"


@pytest.mark.asyncio
async def test_business_request_lookup_uses_authenticated_project(monkeypatch):
    from uuid import uuid4

    request_id, project_id = uuid4(), uuid4()
    app = FastAPI()
    app.include_router(router, prefix="/v1/ai/workflows")
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(project_id=project_id, role="admin")
    lookup = AsyncMock(return_value=[])
    monkeypatch.setattr(workflow_client, "list_business_request_executions", lookup)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            f"/v1/ai/workflows/executions/by-business-request/{request_id}",
            params={"project_id": str(uuid4())},
        )
    assert response.status_code == 200
    assert response.json() == []
    lookup.assert_awaited_once_with(
        str(request_id), str(project_id), limit=20, skip=0,
        before_started_at=None, before_id=None,
    )
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(project_id=project_id, role="user", username="staff-a")
    lookup.reset_mock()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.get(
            f"/v1/ai/workflows/executions/by-business-request/{request_id}",
        )
    assert denied.status_code == 403
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_business_request_client_forwards_only_owned_scope(monkeypatch):
    monkeypatch.setattr("app.services.workflow_client.settings.SECRET_KEY", "test-shared-secret")
    send = AsyncMock(return_value=object())
    read = AsyncMock(return_value=[])
    monkeypatch.setattr(workflow_client, "_make_request", send)
    monkeypatch.setattr(workflow_client, "_handle_response", read)
    assert await workflow_client.list_business_request_executions(
        "request-a", "project-a", limit=7,
    ) == []
    send.assert_awaited_once_with(
        "GET", "/v1/workflows/executions/by-business-request/request-a",
        params={"project_id": "project-a", "limit": 7},
        extra_headers={"X-Internal-API-Key": "test-shared-secret"},
    )

    send.reset_mock()
    assert await workflow_client.list_business_request_executions(
        "request-a", "project-a", limit=7, visitor_session_id="session-a",
    ) == []
    send.assert_awaited_once_with(
        "GET", "/v1/workflows/executions/by-business-request/request-a",
        params={"project_id": "project-a", "limit": 7,
                "visitor_session_id": "session-a"},
        extra_headers={"X-Internal-API-Key": "test-shared-secret"},
    )


@pytest.mark.asyncio
async def test_business_request_client_forwards_page_offset(monkeypatch):
    monkeypatch.setattr("app.services.workflow_client.settings.SECRET_KEY", "test-shared-secret")
    send = AsyncMock(return_value=object())
    monkeypatch.setattr(workflow_client, "_make_request", send)
    monkeypatch.setattr(workflow_client, "_handle_response", AsyncMock(return_value=[]))
    await workflow_client.list_business_request_executions(
        "request-a", "project-a", limit=20, skip=20,
    )
    assert send.await_args.kwargs["params"] == {
        "project_id": "project-a", "limit": 20, "skip": 20,
    }


@pytest.mark.asyncio
async def test_workflow_client_forwards_history_anchor_without_changing_scope(monkeypatch):
    monkeypatch.setattr("app.services.workflow_client.settings.SECRET_KEY", "test-shared-secret")
    send = AsyncMock(return_value=object())
    monkeypatch.setattr(workflow_client, "_make_request", send)
    monkeypatch.setattr(workflow_client, "_handle_response", AsyncMock(return_value=[]))
    await workflow_client.list_business_request_executions(
        "request-a", "project-a", before_started_at="2026-10-14T00:00:00Z",
        before_id="run-b",
    )
    assert send.await_args.kwargs["params"] == {
        "project_id": "project-a", "limit": 20,
        "before_started_at": "2026-10-14T00:00:00Z", "before_id": "run-b",
    }
    assert send.await_args.kwargs["extra_headers"] == {
        "X-Internal-API-Key": "test-shared-secret",
    }


@pytest.mark.asyncio
async def test_visitor_session_lookup_requires_admin_and_owned_project(monkeypatch):
    from uuid import uuid4

    session_id, project_id = uuid4(), uuid4()
    app = FastAPI()
    app.include_router(router, prefix="/v1/ai/workflows")
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(
        project_id=project_id, role="admin",
    )
    lookup = AsyncMock(return_value=[])
    monkeypatch.setattr(workflow_client, "list_visitor_session_executions", lookup, raising=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            f"/v1/ai/workflows/executions/by-visitor-session/{session_id}",
            params={"project_id": str(uuid4())},
        )
    assert response.status_code == 200
    lookup.assert_awaited_once_with(
        str(session_id), str(project_id), limit=20, skip=0,
        before_started_at=None, before_id=None,
    )
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(
        project_id=project_id, role="user", username="staff-a",
    )
    lookup.reset_mock()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.get(f"/v1/ai/workflows/executions/by-visitor-session/{session_id}")
    assert denied.status_code == 403
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_visitor_session_client_uses_private_service_header(monkeypatch):
    monkeypatch.setattr("app.services.workflow_client.settings.SECRET_KEY", "test-shared-secret")
    send = AsyncMock(return_value=object())
    read = AsyncMock(return_value=[])
    monkeypatch.setattr(workflow_client, "_make_request", send)
    monkeypatch.setattr(workflow_client, "_handle_response", read)
    assert await workflow_client.list_visitor_session_executions(
        "session-a", "project-a", limit=7,
    ) == []
    send.assert_awaited_once_with(
        "GET", "/v1/workflows/executions/by-visitor-session/session-a",
        params={"project_id": "project-a", "limit": 7},
        extra_headers={"X-Internal-API-Key": "test-shared-secret"},
    )
