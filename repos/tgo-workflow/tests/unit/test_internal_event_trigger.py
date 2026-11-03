"""A signed internal event creates one durable run of its published workflow."""

import hashlib
import hmac
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import events, executions
from app.database import get_db
from app.models.execution import WorkflowExecution
from app.models.approval import WorkflowApprovalRequest
from app.models.workflow import Workflow
from app.models.workflow_release import WorkflowRelease
from app.services.execution_snapshot import definition_digest
from app.services.event_inputs import frozen_event_inputs
from app.services import event_trigger, event_rate_limit
from app.services import workflow_releases
from app.schemas.workflow_release import WorkflowPublishRequest
from app.engine.executor import WorkflowExecutor
from app.services.execution_lifecycle import ExecutionStopped
from app.services.validation_service import ValidationService
from celery_app import tasks
from datetime import timedelta


def event_definition(event_type="order.shipped"):
    return {"nodes": [
        {"id": "event", "type": "event", "data": {
            "label": "订单发货", "reference_key": "event", "event_type": event_type,
        }},
        {"id": "answer", "type": "answer", "data": {
            "label": "结果", "reference_key": "answer", "output_type": "template",
            "output_template": "已收到事件",
        }},
    ], "edges": [{"source": "event", "target": "answer"}]}


def signed_headers(body: bytes, event_id: str, workflow_id: str, *, timestamp: int | None = None,
                   secret: str = "test-internal-secret") -> dict[str, str]:
    instant = str(timestamp or int(datetime.now(timezone.utc).timestamp()))
    message = (instant.encode() + b"\n" + event_id.encode() + b"\n"
               + workflow_id.encode() + b"\n" + body)
    signature = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return {
        "X-Workflow-Timestamp": instant,
        "X-Workflow-Event-ID": event_id,
        "X-Workflow-Signature": f"sha256={signature}",
        "Content-Type": "application/json",
    }


@pytest.fixture
def event_app(monkeypatch):
    monkeypatch.setattr(events.settings, "SECRET_KEY", SecretStr("test-internal-secret"))
    enqueue = AsyncMock()
    monkeypatch.setattr(event_trigger, "enqueue_pending_initial", enqueue)
    admit = AsyncMock()
    monkeypatch.setattr(event_trigger, "admit_event", admit)
    app = FastAPI()
    app.include_router(events.router, prefix="/v1/workflows")
    app.include_router(executions.router, prefix="/v1/workflows")
    return app, enqueue


@pytest.mark.asyncio
async def test_signed_event_is_durable_idempotent_and_private(event_app):
    app, enqueue = event_app
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Workflow.__table__.create)
        await connection.run_sync(WorkflowRelease.__table__.create)
        await connection.run_sync(WorkflowExecution.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    workflow_id = str(uuid4())
    definition = event_definition()
    async with sessions() as db:
        db.add(Workflow(id=workflow_id, project_id="project-a", name="发货通知",
                        status="draft", definition=definition,
                        version=1, tags=[]))
        await db.commit()
        await workflow_releases.publish(
            db, workflow_id, "project-a",
            WorkflowPublishRequest(expected_draft_version=1,
                                   expected_publication_revision=0),
            "staff-a",
        )
        await db.commit()

    async def session():
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = session
    path = f"/v1/workflows/{workflow_id}/events"
    body = json.dumps({"project_id": "project-a", "event_type": "order.shipped",
                       "data": {"order_ref": "private-order"}}).encode()
    headers = signed_headers(body, "evt-1", workflow_id)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            response = await client.post(path, content=body, headers=headers)
            assert response.status_code == 202, response.text
            assert response.json()["deduplicated"] is False
            assert "private-order" not in response.text
            duplicate = await client.post(path, content=body, headers=headers)
            assert duplicate.status_code == 202
            assert duplicate.json()["execution_id"] == response.json()["execution_id"]
            assert duplicate.json()["deduplicated"] is True
            assert enqueue.await_count == 1
            other_body = json.dumps({"project_id": "project-a", "event_type": "order.shipped",
                                     "data": {"order_ref": "other"}}).encode()
            conflict = await client.post(path, content=other_body,
                                         headers=signed_headers(other_body, "evt-1", workflow_id))
            assert conflict.status_code == 409
            other_type = json.dumps({"project_id": "project-a", "event_type": "order.cancelled",
                                     "data": {}}).encode()
            assert (await client.post(path, content=other_type,
                                      headers=signed_headers(other_type, "evt-2", workflow_id))).status_code == 409
            event_trigger.admit_event.side_effect = HTTPException(429, "limited")
            assert (await client.post(path, content=body,
                                      headers=signed_headers(body, "evt-3", workflow_id))).status_code == 429
            event_trigger.admit_event.side_effect = None
            generic = await client.post(f"/v1/workflows/{workflow_id}/execute?project_id=project-a",
                                        json={"execution_mode": "production", "async": True,
                                              "inputs": {"data": {"order_ref": "private-order"}}})
            assert generic.status_code == 403
            async with sessions() as db:
                runs = (await db.scalars(select(WorkflowExecution))).all()
                assert len(runs) == 1
                assert runs[0].status == "pending"
                assert runs[0].source_channel == "internal_event"
                assert frozen_event_inputs(runs[0]) == {"data": {"order_ref": "private-order"}}
                assert runs[0].definition_snapshot == definition
                runs[0].input = None
                runs[0].status = "completed"
                await db.commit()
            scrubbed_retry = await client.post(path, content=body, headers=headers)
            assert scrubbed_retry.status_code == 202
            assert scrubbed_retry.json()["deduplicated"] is True
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_event_rejects_unsigned_and_stale_requests(event_app):
    app, enqueue = event_app
    body = json.dumps({"project_id": "project-a", "event_type": "order.shipped",
                       "data": {}}).encode()
    workflow_id = str(uuid4())
    path = f"/v1/workflows/{workflow_id}/events"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        assert (await client.post(path, content=body)).status_code == 403
        bad_signature = signed_headers(body, "evt-1", workflow_id, secret="wrong")
        assert (await client.post(path, content=body, headers=bad_signature)).status_code == 403
        stale = signed_headers(body, "evt-1", workflow_id, timestamp=1)
        assert (await client.post(path, content=body, headers=stale)).status_code == 403
        signed_for_other_workflow = signed_headers(body, "evt-1", str(uuid4()))
        assert (await client.post(path, content=body,
                                  headers=signed_for_other_workflow)).status_code == 403
        oversized = b" " * (events.MAX_EVENT_BYTES + 1)
        assert (await client.post(path, content=oversized,
                                  headers=signed_headers(oversized, "evt-2", workflow_id))).status_code == 413
    enqueue.assert_not_awaited()


def test_event_node_requires_named_event_and_remains_distinct_from_timer():
    assert ValidationService.validate_workflow(event_definition()) == []
    bad = event_definition("  ")
    assert any("event_type" in error for error in ValidationService.validate_workflow(bad))


@pytest.mark.asyncio
async def test_event_rate_limit_is_atomic_and_fails_closed(monkeypatch):
    client = AsyncMock()
    client.eval.return_value = 0
    monkeypatch.setattr(event_rate_limit.Redis, "from_url", lambda *args, **kwargs: client)
    with pytest.raises(HTTPException) as limited:
        await event_rate_limit.admit_event("project-a", "workflow-a", "event-a")
    assert limited.value.status_code == 429
    assert client.eval.await_count == 1
    assert client.aclose.await_count == 1
    client.eval.side_effect = OSError("redis credential is private")
    with pytest.raises(HTTPException) as unavailable:
        await event_rate_limit.admit_event("project-a", "workflow-a", "event-b")
    assert unavailable.value.status_code == 503
    assert "private" not in str(unavailable.value.detail)


@pytest.mark.asyncio
async def test_event_data_reaches_workflow_answer_without_extra_nodes():
    definition = event_definition()
    definition["nodes"][1]["data"] = {
        "label": "结果", "reference_key": "answer", "output_type": "variable",
        "output_variable": "event.data",
    }
    result = await WorkflowExecutor(definition, project_id="project-a").run(
        {"data": {"order_ref": "test-order"}},
    )
    assert result == {"order_ref": "test-order"}


@pytest.mark.asyncio
async def test_pending_event_is_recovered_after_lost_broker_ack(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'events.db').as_posix()}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    definition = event_definition()
    workflow_id = str(uuid4())
    started = datetime.now(timezone.utc) - timedelta(minutes=3)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Workflow.__table__.create)
            await connection.run_sync(WorkflowExecution.__table__.create)
        async with sessions() as db:
            db.add(Workflow(id=workflow_id, project_id="project-a", name="发货通知",
                            status="active", definition=definition, version=1, tags=[]))
            db.add(WorkflowExecution(
                id=str(uuid4()), project_id="project-a", workflow_id=workflow_id,
                status="pending", execution_mode="production",
                source_channel="internal_event", source_message_id="evt-1",
                release_id=str(uuid4()), definition_snapshot=definition,
                definition_digest=definition_digest(definition), workflow_version=1,
                input={"data": {}}, started_at=started,
            ))
            await db.commit()
        monkeypatch.setattr(tasks, "workflow_task_session", sessions)
        publish = AsyncMock()
        monkeypatch.setattr(tasks, "enqueue_pending_initial", publish)
        assert await tasks.sweep_pending_initial() == 1
        assert publish.await_count == 1
        assert await tasks.sweep_pending_initial() == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stopped_workflow_cancels_pending_event_execution():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    definition = event_definition()
    workflow_id, run_id = str(uuid4()), str(uuid4())
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Workflow.__table__.create)
            await connection.run_sync(WorkflowExecution.__table__.create)
            await connection.run_sync(WorkflowApprovalRequest.__table__.create)
        async with sessions() as db:
            db.add(Workflow(id=workflow_id, project_id="project-a", name="发货通知",
                            status="inactive", definition=definition, version=1, tags=[]))
            db.add(WorkflowExecution(id=run_id, project_id="project-a",
                                     workflow_id=workflow_id, status="running",
                                     source_channel="internal_event", input={"data": {}}))
            await db.commit()
            with pytest.raises(ExecutionStopped):
                await event_trigger.require_active_internal_event(
                    db, "project-a", workflow_id, run_id,
                )
            assert (await db.get(WorkflowExecution, run_id)).status == "cancelled"
    finally:
        await engine.dispose()
