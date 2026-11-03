"""Publication identity and optimistic versions survive the authenticated proxy."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.endpoints.ai_workflows import router, workflow_client
from app.core.security import get_current_active_user


@pytest.mark.asyncio
async def test_history_is_scoped_and_does_not_accept_a_client_project_override(monkeypatch):
    staff = SimpleNamespace(id=uuid4(), project_id=uuid4(), role="admin")
    upstream = AsyncMock(return_value=[])
    monkeypatch.setattr(workflow_client, "workflow_releases", upstream)
    app = FastAPI()
    app.include_router(router, prefix="/v1/ai/workflows")
    app.dependency_overrides[get_current_active_user] = lambda: staff
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/ai/workflows/owned-flow/releases", params={"project_id": str(uuid4())})
        assert response.status_code == 200
    upstream.assert_awaited_once_with("owned-flow", str(staff.project_id))


@pytest.mark.asyncio
async def test_publication_requires_admin_and_explicit_revisions(monkeypatch):
    upstream = AsyncMock()
    monkeypatch.setattr(workflow_client, "publish_workflow", upstream)
    app = FastAPI()
    app.include_router(router, prefix="/v1/ai/workflows")
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(id=uuid4(), project_id=uuid4(), role="user", username="test-user")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.post("/v1/ai/workflows/flow/publish", json={"expected_draft_version": 2, "expected_publication_revision": 1})
        assert denied.status_code == 403
        app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(id=uuid4(), project_id=uuid4(), role="admin")
        missing = await client.post("/v1/ai/workflows/flow/publish", json={})
        assert missing.status_code == 422
        forged = await client.post("/v1/ai/workflows/flow/publish", json={"expected_draft_version": 2, "expected_publication_revision": 1, "project_id": str(uuid4())})
        assert forged.status_code == 422
    upstream.assert_not_awaited()
