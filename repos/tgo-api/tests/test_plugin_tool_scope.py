"""Public plugin execution derives tenant identity from the signed-in staff."""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.endpoints import plugin_tools
from app.core.database import get_db
from app.core.security import get_current_active_user
from app.models import Staff
from app.models.visitor import Visitor


@pytest.mark.asyncio
@pytest.mark.parametrize("visitor", [None, "missing", "invalid"])
async def test_plugin_proxy_uses_staff_project_and_rejects_unknown_visitor(monkeypatch, visitor):
    staff = Staff(id=uuid4(), project_id=uuid4(), username="fixture", password_hash="unused", role="admin")
    db = Mock()
    db.query.return_value.filter.return_value.first.return_value = None
    call = AsyncMock(return_value={"success": True, "content": "ok"})
    monkeypatch.setattr(plugin_tools.plugin_runtime_client, "execute_tool", call)
    app = FastAPI()
    app.include_router(plugin_tools.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_active_user] = lambda: staff
    visitor_id = str(uuid4()) if visitor == "missing" else visitor
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/execute/fixture/query", params={"project_id": "foreign"},
                                     json={"arguments": {}, "context": {"visitor_id": visitor_id}})
    assert response.status_code == {None: 200, "missing": 404, "invalid": 400}[visitor]
    if visitor is None:
        assert call.await_args.kwargs["project_id"] == str(staff.project_id)
    else:
        call.assert_not_awaited()
        if visitor == "missing":
            clauses = db.query.return_value.filter.call_args.args
            assert any("project_id" in str(clause) and staff.project_id in clause.compile().params.values()
                       for clause in clauses)


@pytest.mark.asyncio
async def test_plugin_proxy_requires_login(monkeypatch):
    call = AsyncMock()
    monkeypatch.setattr(plugin_tools.plugin_runtime_client, "execute_tool", call)
    app = FastAPI()
    app.include_router(plugin_tools.router)
    app.dependency_overrides[get_db] = lambda: Mock()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/execute/fixture/query", json={"arguments": {}, "context": {}})
    assert response.status_code in (401, 403)
    call.assert_not_awaited()


@pytest.mark.asyncio
async def test_plugin_proxy_forwards_owned_visitor(monkeypatch):
    staff = Staff(id=uuid4(), project_id=uuid4(), username="fixture", password_hash="unused", role="admin")
    visitor = Visitor(id=uuid4(), project_id=staff.project_id, name="测试客户")
    db = Mock()
    db.query.return_value.filter.return_value.first.return_value = visitor
    call = AsyncMock(return_value={"success": True, "content": "ok"})
    monkeypatch.setattr(plugin_tools.plugin_runtime_client, "execute_tool", call)
    app = FastAPI()
    app.include_router(plugin_tools.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_active_user] = lambda: staff
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/execute/fixture/query", json={
            "arguments": {}, "context": {"visitor_id": str(visitor.id)},
        })
    assert response.status_code == 200
    assert call.await_args.kwargs["project_id"] == str(staff.project_id)
    assert call.await_args.args[2]["context"]["visitor_id"] == str(visitor.id)
