"""The public device reference read derives tenant from the signed-in staff."""

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
async def test_device_reference_gateway_uses_authenticated_project(monkeypatch):
    project_id, device_id = uuid4(), uuid4()
    app = FastAPI()
    app.include_router(router, prefix="/v1/ai/workflows")
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(project_id=project_id)
    read = AsyncMock(return_value={"references": [
        {"workflow_id": str(uuid4()), "name": "订单查询", "source": "published"},
    ], "total": 1})
    monkeypatch.setattr(workflow_client, "list_device_references", read)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/ai/workflows/device-references", params={
            "device_id": str(device_id), "project_id": str(uuid4()), "limit": 5,
        })
    assert response.status_code == 200
    assert response.json()["total"] == 1
    read.assert_awaited_once_with(str(project_id), str(device_id), limit=5, offset=0)


@pytest.mark.asyncio
async def test_device_reference_client_only_sends_scoped_identifiers(monkeypatch):
    response = object()
    send = AsyncMock(return_value=response)
    monkeypatch.setattr(workflow_client, "_make_request", send)
    monkeypatch.setattr(workflow_client, "_handle_response", AsyncMock(return_value={
        "references": [], "total": 0,
    }))
    await workflow_client.list_device_references("project-a", str(uuid4()), limit=5)
    assert send.await_args.args == ("GET", "/v1/workflows/device-references")
    assert send.await_args.kwargs["params"]["project_id"] == "project-a"
    assert send.await_args.kwargs["params"]["limit"] == 5
