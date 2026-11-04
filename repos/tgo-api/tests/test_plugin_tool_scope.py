"""Public plugin execution derives tenant identity from the signed-in staff."""

from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session

from app.api.v1.endpoints import plugin_tools
from app.core.database import get_db
from app.core.security import create_access_token
from app.core.exceptions import TGOAPIException, tgo_api_exception_handler
from app.services import visitor_chat_access
from tests.test_im_access import records as records
from app.models.visitor import Visitor


@pytest.mark.asyncio
@pytest.mark.parametrize("visitor", [None, "missing", "invalid"])
async def test_plugin_proxy_uses_staff_project_and_rejects_unknown_visitor(
    monkeypatch, visitor, records
):
    db, projects, all_staff, _, _ = records
    staff = all_staff[0]
    token = create_access_token(staff.username, projects[0].id, staff.role)
    monkeypatch.setattr(
        visitor_chat_access, "SessionLocal", lambda: Session(db.get_bind())
    )
    call = AsyncMock(return_value={"success": True, "content": "ok"})
    monkeypatch.setattr(plugin_tools.plugin_runtime_client, "execute_tool", call)
    app = FastAPI()
    app.add_exception_handler(TGOAPIException, tgo_api_exception_handler)
    app.include_router(plugin_tools.router)
    app.dependency_overrides[get_db] = lambda: db
    visitor_id = str(uuid4()) if visitor == "missing" else visitor
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/execute/fixture/query",
            params={"project_id": "foreign"},
            json={"arguments": {}, "context": {"visitor_id": visitor_id}},
            headers={"Authorization": "Bearer " + token},
        )
    assert response.status_code == {None: 200, "missing": 404, "invalid": 400}[visitor]
    if visitor is None:
        assert call.await_args.kwargs["project_id"] == str(staff.project_id)
    else:
        call.assert_not_awaited()
        if visitor == "missing":
            assert (
                db.query(Visitor)
                .filter(
                    Visitor.id == UUID(visitor_id), Visitor.project_id == staff.project_id
                )
                .count()
                == 0
            )


@pytest.mark.asyncio
async def test_plugin_proxy_requires_login(monkeypatch):
    call = AsyncMock()
    monkeypatch.setattr(plugin_tools.plugin_runtime_client, "execute_tool", call)
    app = FastAPI()
    app.include_router(plugin_tools.router)
    app.dependency_overrides[get_db] = lambda: Mock()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/execute/fixture/query", json={"arguments": {}, "context": {}}
        )
    assert response.status_code in (401, 403)
    call.assert_not_awaited()


@pytest.mark.asyncio
async def test_plugin_proxy_forwards_owned_visitor(monkeypatch, records):
    db, projects, all_staff, _, all_visitors = records
    staff, visitor = all_staff[0], all_visitors[0]
    token = create_access_token(staff.username, projects[0].id, staff.role)
    monkeypatch.setattr(
        visitor_chat_access, "SessionLocal", lambda: Session(db.get_bind())
    )
    call = AsyncMock(return_value={"success": True, "content": "ok"})
    monkeypatch.setattr(plugin_tools.plugin_runtime_client, "execute_tool", call)
    app = FastAPI()
    app.add_exception_handler(TGOAPIException, tgo_api_exception_handler)
    app.include_router(plugin_tools.router)
    app.dependency_overrides[get_db] = lambda: db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/execute/fixture/query",
            json={
                "arguments": {},
                "context": {"visitor_id": str(visitor.id)},
            },
            headers={"Authorization": "Bearer " + token},
        )
    assert response.status_code == 200
    assert call.await_args.kwargs["project_id"] == str(staff.project_id)
    assert call.await_args.args[2]["context"]["visitor_id"] == str(visitor.id)
