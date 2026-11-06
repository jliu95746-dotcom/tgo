"""The admin proxy keeps child-run lookup inside the signed-in project."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.endpoints.ai_workflows import router
from app.core.security import get_current_active_user
from app.services.workflow_client import workflow_client


@pytest.mark.asyncio
async def test_child_lookup_requires_admin_and_ignores_supplied_project(
    monkeypatch,
):
    parent_id, project_id = uuid4(), uuid4()
    app = FastAPI()
    app.include_router(router, prefix="/v1/ai/workflows")
    app.dependency_overrides[
        get_current_active_user
    ] = lambda: SimpleNamespace(
        project_id=project_id,
        role="admin",
    )
    lookup = AsyncMock(return_value=[])
    monkeypatch.setattr(
        workflow_client, "list_child_executions", lookup, raising=False
    )
    path = f"/v1/ai/workflows/executions/by-parent/{parent_id}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            path, params={"project_id": str(uuid4()), "skip": 20}
        )
        assert response.status_code == 200
        lookup.assert_awaited_once_with(
            str(parent_id),
            str(project_id),
            limit=20,
            skip=20,
            before_started_at=None,
            before_id=None,
        )
        app.dependency_overrides[
            get_current_active_user
        ] = lambda: SimpleNamespace(
            project_id=project_id,
            role="user",
            username="staff-a",
        )
        lookup.reset_mock()
        denied = await client.get(path)
        assert denied.status_code == 403
        lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_child_lookup_passes_cursor_in_authenticated_project(
    monkeypatch,
):
    parent_id, project_id, cursor_id = uuid4(), uuid4(), uuid4()
    app = FastAPI()
    app.include_router(router, prefix="/v1/ai/workflows")
    app.dependency_overrides[
        get_current_active_user
    ] = lambda: SimpleNamespace(
        project_id=project_id,
        role="admin",
    )
    lookup = AsyncMock(return_value=[])
    monkeypatch.setattr(workflow_client, "list_child_executions", lookup)
    timestamp = "2026-11-06T12:00:00Z"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            f"/v1/ai/workflows/executions/by-parent/{parent_id}",
            params={
                "project_id": str(uuid4()),
                "before_started_at": timestamp,
                "before_id": str(cursor_id),
            },
        )
    assert response.status_code == 200
    lookup.assert_awaited_once_with(
        str(parent_id),
        str(project_id),
        limit=20,
        skip=0,
        before_started_at=timestamp,
        before_id=str(cursor_id),
    )


@pytest.mark.asyncio
async def test_child_lookup_client_sends_private_identity_and_offset(
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.workflow_client.settings.SECRET_KEY",
        "test-shared-secret",
    )
    send = AsyncMock(return_value=object())
    monkeypatch.setattr(workflow_client, "_make_request", send)
    monkeypatch.setattr(
        workflow_client, "_handle_response", AsyncMock(return_value=[])
    )
    assert (
        await workflow_client.list_child_executions(
            "parent-a",
            "owned-project",
            limit=7,
            skip=14,
        )
        == []
    )
    send.assert_awaited_once_with(
        "GET",
        "/v1/workflows/executions/by-parent/parent-a",
        params={"project_id": "owned-project", "limit": 7, "skip": 14},
        extra_headers={"X-Internal-API-Key": "test-shared-secret"},
    )
