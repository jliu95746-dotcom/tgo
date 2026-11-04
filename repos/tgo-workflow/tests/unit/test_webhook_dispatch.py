"""Broker retries cannot replace frozen data or bypass source checks."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
import json

import pytest
from sqlalchemy import func, select, update

from app.models.execution import WorkflowExecution, NodeExecution
from app.models.workflow_webhook import WorkflowWebhook
from app.models.workflow_webhook_delivery import WorkflowWebhookDelivery
from app.services import (
    webhook_dispatch as dispatch,
    webhook_runtime as runtime,
)
from app.services.workflow_dispatch import WorkflowInitialDispatchError
from celery_app import tasks
from tests.unit import test_webhook_ingress as ingress_fixtures

fixture = ingress_fixtures.fixture
accept = ingress_fixtures.accept


async def queued(fixture, monkeypatch):
    monkeypatch.setattr(dispatch, "verify_webhook_authority", AsyncMock())
    monkeypatch.setattr(dispatch, "enqueue_pending_initial", AsyncMock())
    receipt = await accept(fixture)
    async with fixture.sessions() as db:
        identifier = await dispatch.dispatch_delivery(
            db, str(receipt.delivery_id)
        )

    @asynccontextmanager
    async def session():
        async with fixture.sessions() as db:
            yield db

    monkeypatch.setattr(tasks, "workflow_task_session", session)
    monkeypatch.setattr(runtime, "verify_webhook_authority", AsyncMock())
    return identifier, receipt.delivery_id


@pytest.mark.asyncio
async def test_duplicate_dispatch_and_real_worker_run_once(
    fixture, monkeypatch
):
    identifier, source = await queued(fixture, monkeypatch)
    async with fixture.sessions() as db:
        assert await dispatch.dispatch_delivery(db, str(source)) is None
        run = await db.get(WorkflowExecution, identifier)
        assert run.input == {"_webhook_delivery_id": str(source)}
        assert "synthetic-private-question" not in json.dumps(run.input)
    await tasks._run_workflow(
        identifier,
        fixture.workflow_id,
        {"data": {"question": "FORGED"}},
        str(fixture.project_id),
    )
    await tasks._run_workflow(
        identifier, fixture.workflow_id, {}, str(fixture.project_id)
    )
    async with fixture.sessions() as db:
        run = await db.get(WorkflowExecution, identifier)
        row = await db.get(WorkflowWebhookDelivery, str(source))
        assert run.status == "completed" and run.input is None
        assert row.status == "completed" and row.payload_ciphertext is None
        assert "FORGED" not in json.dumps(run.output)
        assert "synthetic-private-question" in json.dumps(run.output)
        assert (
            await db.scalar(select(func.count()).select_from(NodeExecution))
            == 2
        )


@pytest.mark.asyncio
async def test_broker_ack_loss_keeps_a_recoverable_pending_execution(
    fixture, monkeypatch
):
    monkeypatch.setattr(dispatch, "verify_webhook_authority", AsyncMock())
    monkeypatch.setattr(
        dispatch,
        "enqueue_pending_initial",
        AsyncMock(side_effect=WorkflowInitialDispatchError("unknown")),
    )
    receipt = await accept(fixture)
    async with fixture.sessions() as db:
        identifier = await dispatch.dispatch_delivery(
            db, str(receipt.delivery_id)
        )
        run = await db.get(WorkflowExecution, identifier)
        assert run.status == "pending"
        assert run.input == {"_webhook_delivery_id": str(receipt.delivery_id)}


@pytest.mark.asyncio
async def test_permission_wait_cannot_outlive_disable(fixture, monkeypatch):
    receipt = await accept(fixture)

    async def disable(query):
        async with fixture.sessions() as db:
            await db.execute(
                update(WorkflowWebhook).values(enabled=False, revision=3)
            )
            await db.commit()

    monkeypatch.setattr(dispatch, "verify_webhook_authority", disable)
    async with fixture.sessions() as db:
        assert (
            await dispatch.dispatch_delivery(db, str(receipt.delivery_id))
            is None
        )
        row = await db.get(WorkflowWebhookDelivery, str(receipt.delivery_id))
        assert row.status == "cancelled" and row.payload_ciphertext is None


@pytest.mark.asyncio
async def test_authority_outage_retries_without_creating_an_execution(
    fixture, monkeypatch
):
    receipt = await accept(fixture)
    monkeypatch.setattr(
        dispatch,
        "verify_webhook_authority",
        AsyncMock(side_effect=RuntimeError()),
    )
    async with fixture.sessions() as db:
        assert (
            await dispatch.dispatch_delivery(db, str(receipt.delivery_id))
            is None
        )
        row = await db.get(WorkflowWebhookDelivery, str(receipt.delivery_id))
        assert row.status == "pending" and row.payload_ciphertext
        assert row.attempts == 1
        assert (
            await db.scalar(
                select(func.count()).select_from(WorkflowExecution)
            )
            == 0
        )


@pytest.mark.asyncio
async def test_expired_pending_payload_is_removed(fixture, monkeypatch):
    receipt = await accept(fixture)
    async with fixture.sessions() as db:
        await db.execute(
            update(WorkflowWebhookDelivery).values(
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)
            )
        )
        await db.commit()
        assert (
            await dispatch.dispatch_delivery(db, str(receipt.delivery_id))
            is None
        )
        row = await db.get(WorkflowWebhookDelivery, str(receipt.delivery_id))
        assert row.status == "expired" and row.payload_ciphertext is None


@pytest.mark.asyncio
async def test_revocation_between_nodes_stops_and_clears_input(
    fixture, monkeypatch
):
    identifier, source = await queued(fixture, monkeypatch)
    monkeypatch.setattr(
        runtime,
        "verify_webhook_authority",
        AsyncMock(side_effect=[None, None, PermissionError()]),
    )
    await tasks._run_workflow(
        identifier, fixture.workflow_id, {}, str(fixture.project_id)
    )
    async with fixture.sessions() as db:
        run = await db.get(WorkflowExecution, identifier)
        row = await db.get(WorkflowWebhookDelivery, str(source))
        assert (
            run.status == "cancelled"
            and run.input is None
            and run.output is None
        )
        assert row.payload_ciphertext is None


@pytest.mark.asyncio
async def test_broker_messages_contain_only_reference_not_body(
    fixture, monkeypatch
):
    identifier, _ = await queued(fixture, monkeypatch)
    from app.services.workflow_dispatch import enqueue_pending_initial

    captured = []
    monkeypatch.setattr(
        tasks.execute_workflow_task,
        "apply_async",
        lambda **kwargs: captured.append(kwargs),
    )
    async with fixture.sessions() as db:
        run = await db.get(WorkflowExecution, identifier)
        await enqueue_pending_initial(run)
    assert captured[0]["args"][2] == {}
