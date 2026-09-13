"""A 202 cancellation response requires worker confirmation, never a queued flag."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.endpoints import ai_runs
from app.core.database import get_db
from app.core.security import get_current_active_user
from app.schemas.ai_runs import ReplyRun
from app.services import ai_reply_control as control
from app.services import reply_cancellation as cancellation
from app.services.run_registry import RegistryUnavailable

pytestmark = pytest.mark.asyncio


def client(project, *, staff=True, database=None):
    app = FastAPI()
    app.include_router(ai_runs.router, prefix="/runs")
    if staff:
        app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(
            project_id=project
        )
    if database is not None:
        app.dependency_overrides[get_db] = lambda: database
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://owned.test"
    )


async def test_staff_receives_success_only_after_owner_confirms():
    project = uuid4()
    item = ReplyRun(
        project_id=str(project),
        client_msg_no="owned",
        channel_id="owned-vtr",
        channel_type=251,
    )
    await control.run_registry.start(item)

    async def worker():
        while (
            await control.run_registry.get(str(project), "owned")
        ).status != "cancel_requested":
            await asyncio.sleep(0)
        await control.run_registry.finish(item, "cancelled")

    task = asyncio.create_task(worker())
    try:
        async with client(project) as http:
            response = await asyncio.wait_for(
                http.post("/runs/cancel", json={"client_msg_no": "owned"}), 1
            )
            assert response.status_code == 202
            assert response.json() == {
                "accepted": True,
                "status": "cancelled",
                "client_msg_no": "owned",
            }
            assert (
                await http.post("/runs/cancel", json={"client_msg_no": "owned"})
            ).status_code == 202
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize(
    "case", ["missing", "foreign", "publishing", "unconfirmed", "pending"]
)
async def test_missing_finished_or_unconfirmed_is_not_success(case, monkeypatch):
    monkeypatch.setattr(cancellation, "CANCEL_WAIT_SECONDS", 0.02)
    project = uuid4()
    item = ReplyRun(
        project_id=str(uuid4() if case == "foreign" else project),
        client_msg_no="owned",
        channel_id="owned-vtr",
        channel_type=251,
    )
    if case != "missing":
        await control.run_registry.start(item)
    if case == "publishing":
        await control.run_registry.begin_publication(item)
    if case == "unconfirmed":
        await control.run_registry.finish(item, "failed", "upstream_stop_unconfirmed")
    async with client(project) as http:
        response = await http.post("/runs/cancel", json={"client_msg_no": "owned"})
    assert (
        response.status_code
        == {
            "missing": 404,
            "foreign": 404,
            "publishing": 409,
            "unconfirmed": 503,
            "pending": 504,
        }[case]
    )


async def test_staff_endpoint_requires_authentication():
    async with client(uuid4(), staff=False) as http:
        response = await http.post("/runs/cancel", json={"client_msg_no": "owned"})
    assert response.status_code in (401, 403)


async def test_platform_key_cannot_cancel_staff_channel():
    project, platform_id = uuid4(), uuid4()
    item = ReplyRun(
        project_id=str(project),
        client_msg_no="owned",
        channel_id="staff",
        channel_type=1,
    )
    await control.run_registry.start(item)
    database = Mock()
    database.query.return_value.filter.return_value.first.return_value = (
        SimpleNamespace(project_id=project, id=platform_id)
    )
    async with client(project, database=database) as http:
        response = await http.post(
            "/runs/cancel-by-client",
            json={"client_msg_no": "owned", "platform_api_key": "fixture"},
        )
    assert response.status_code == 404
    assert (await control.run_registry.get(str(project), "owned")).status == "active"


@pytest.mark.parametrize("matched", [True, False])
async def test_visitor_stop_requires_matching_project_and_platform(matched):
    project, platform_id, visitor_id = uuid4(), uuid4(), uuid4()
    item = ReplyRun(
        project_id=str(project),
        client_msg_no="owned",
        channel_id=f"{visitor_id}-vtr",
        channel_type=251,
    )
    await control.run_registry.start(item)
    await control.run_registry.request_cancel(item)
    await control.run_registry.finish(item, "cancelled")
    platform_query, visitor_query = Mock(), Mock()
    platform_query.filter.return_value.first.return_value = SimpleNamespace(
        project_id=project,
        id=platform_id,
    )
    visitor_query.filter.return_value.first.return_value = (
        SimpleNamespace(id=visitor_id) if matched else None
    )
    database = Mock()
    database.query.side_effect = [platform_query, visitor_query]
    async with client(project, database=database) as http:
        response = await http.post(
            "/runs/cancel-by-client",
            json={"client_msg_no": "owned", "platform_api_key": "fixture"},
        )
    assert response.status_code == (202 if matched else 404)
    criteria = visitor_query.filter.call_args.args
    assert len(criteria) == 4
    for clause, expected in zip(criteria[:3], (visitor_id, project, platform_id)):
        assert list(clause.compile().params.values()) == [expected]
    assert "deleted_at IS NULL" in str(criteria[3])


async def test_registry_failure_returns_unavailable_not_success(monkeypatch):
    monkeypatch.setattr(
        control.run_registry,
        "get",
        AsyncMock(side_effect=RegistryUnavailable()),
    )
    async with client(uuid4()) as http:
        response = await http.post("/runs/cancel", json={"client_msg_no": "owned"})
    assert response.status_code == 503


async def test_invalid_platform_key_is_rejected_without_changing_run():
    database = Mock()
    database.query.return_value.filter.return_value.first.return_value = None
    async with client(uuid4(), database=database) as http:
        response = await http.post(
            "/runs/cancel-by-client",
            json={"client_msg_no": "owned", "platform_api_key": "fixture"},
        )
    assert response.status_code == 401
