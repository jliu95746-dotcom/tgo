"""Administrator previews bind event tests to the exact published release."""

import json
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import events
from app.database import get_db
from app.models.execution import WorkflowExecution
from app.models.workflow import Workflow
from app.models.workflow_release import WorkflowRelease
from app.schemas.workflow_release import WorkflowPublishRequest
from app.services import event_trigger, workflow_releases
from tests.unit.test_internal_event_trigger import event_definition, signed_headers


@pytest_asyncio.fixture
async def published_event(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        for model in (Workflow, WorkflowRelease, WorkflowExecution):
            await connection.run_sync(model.__table__.create)
    workflow_id = str(uuid4())
    async with sessions() as db:
        db.add(Workflow(id=workflow_id, project_id="project-a", name="咨询归档",
                        status="draft", definition=event_definition(), version=1, tags=[]))
        await db.commit()
        await workflow_releases.publish(db, workflow_id, "project-a", WorkflowPublishRequest(
            expected_draft_version=1, expected_publication_revision=0,
        ), "staff-a")
        await db.commit()
    monkeypatch.setattr(events.settings, "SECRET_KEY", SecretStr("test-internal-secret"))
    enqueue = AsyncMock()
    monkeypatch.setattr(event_trigger, "enqueue_pending_initial", enqueue)
    monkeypatch.setattr(event_trigger, "admit_event", AsyncMock())
    app = FastAPI()
    app.include_router(events.router, prefix="/v1/workflows")

    async def session():
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = session
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            yield client, workflow_id, sessions, enqueue
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_context_requires_internal_key_and_owned_active_publication(published_event):
    client, workflow_id, sessions, _ = published_event
    path = f"/v1/workflows/{workflow_id}/event-test-context"
    headers = {"X-Internal-API-Key": "test-internal-secret"}
    assert (await client.get(path, params={"project_id": "project-a"})).status_code == 403
    assert (await client.get(path, params={"project_id": "other"}, headers=headers)).status_code == 404
    response = await client.get(path, params={"project_id": "project-a"}, headers=headers)
    assert response.status_code == 200, response.text
    context = response.json()
    assert context["event_type"] == "order.shipped"
    assert context["version"] == 1 and context["status"] == "active"
    assert len(context["release_digest"]) == 64
    assert response.headers["cache-control"] == "no-store"
    assert "definition" not in context and "test-internal-secret" not in response.text
    async with sessions() as db:
        workflow = await db.get(Workflow, workflow_id)
        workflow.definition = event_definition("draft.only")
        await db.commit()
    unchanged = await client.get(path, params={"project_id": "project-a"}, headers=headers)
    assert unchanged.json() == context
    async with sessions() as db:
        workflow = await db.get(Workflow, workflow_id)
        workflow.status = "inactive"
        await db.commit()
    assert (await client.get(path, params={"project_id": "project-a"}, headers=headers)).status_code == 409


@pytest.mark.asyncio
async def test_bound_event_refuses_changed_release_and_replays_original_receipt(published_event):
    client, workflow_id, sessions, enqueue = published_event
    context = (await client.get(f"/v1/workflows/{workflow_id}/event-test-context",
        params={"project_id": "project-a"},
        headers={"X-Internal-API-Key": "test-internal-secret"})).json()
    path = f"/v1/workflows/{workflow_id}/events"
    payload = {"project_id": "project-a", "event_type": context["event_type"],
               "expected_release_id": context["release_id"],
               "expected_release_digest": context["release_digest"],
               "data": {"咨询": "材质"}}

    async def send(value, event_id="test-1"):
        body = json.dumps(value).encode()
        return await client.post(path, content=body, headers=signed_headers(body, event_id, workflow_id))

    assert (await send({**payload, "expected_release_digest": "0" * 64})).status_code == 409
    assert (await send({**payload, "expected_release_id": None})).status_code == 422
    accepted = await send(payload)
    assert accepted.status_code == 202, accepted.text
    async with sessions() as db:
        await workflow_releases.publish(db, workflow_id, "project-a", WorkflowPublishRequest(
            expected_draft_version=1, expected_publication_revision=1,
        ), "staff-a")
        runs = (await db.scalars(select(WorkflowExecution))).all()
        assert len(runs) == 1
        runs[0].input = None
        runs[0].status = "completed"
        await db.commit()
    retry = await send(payload)
    assert retry.status_code == 202 and retry.json()["deduplicated"] is True
    assert retry.json()["execution_id"] == accepted.json()["execution_id"]
    assert (await send(payload, "new-test")).status_code == 409
    assert (await send({**payload, "expected_release_id": "changed"})).status_code == 409
    enqueue.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["NaN", "Infinity", "-Infinity", "1e999"])
async def test_nonfinite_nested_event_data_returns_422_before_persistence(published_event, invalid):
    client, workflow_id, _, enqueue = published_event
    body = ('{"project_id":"project-a","event_type":"order.shipped",'
            '"data":{"nested":[{"number":' + invalid + '}]}}').encode()
    response = await client.post(f"/v1/workflows/{workflow_id}/events", content=body,
                                headers=signed_headers(body, "invalid", workflow_id))
    assert response.status_code == 422
    enqueue.assert_not_awaited()
