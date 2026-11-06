"""Internal HTTP keeps signed bytes intact and publishes references only."""

from fastapi import FastAPI
import httpx
from pydantic import SecretStr
import pytest

from app.api import webhook_deliveries as routes, executions
from app.config import settings
from app.database import get_db
from app.services.webhook_http import SIGNATURE_HEADERS
from celery_app.webhook_tasks import dispatch_webhook_delivery_task
from tests.unit import test_webhook_ingress as ingress_fixtures

fixture = ingress_fixtures.fixture


@pytest.mark.asyncio
async def test_real_signed_bytes_survive_http_and_queue_gets_no_body(
    fixture, monkeypatch
):
    monkeypatch.setattr(
        settings, "SECRET_KEY", SecretStr("synthetic-private-http-key")
    )
    application = FastAPI()
    application.include_router(routes.router, prefix="/v1/webhook-deliveries")

    async def database():
        async with fixture.sessions() as db:
            yield db

    application.dependency_overrides[get_db] = database
    published = []
    monkeypatch.setattr(
        dispatch_webhook_delivery_task,
        "apply_async",
        lambda **kwargs: published.append(kwargs),
    )
    body, values = ingress_fixtures.signed(fixture)
    headers = {
        SIGNATURE_HEADERS[name]: value for name, value in values.items()
    }
    headers.update(
        {
            "Content-Type": "application/json",
            "X-Internal-API-Key": "synthetic-private-http-key",
        }
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        path = "/v1/webhook-deliveries/" + str(fixture.hook_id)
        denied = await client.post(path, content=body)
        assert denied.status_code == 403
        first = await client.post(path, content=body, headers=headers)
        assert (
            first.status_code == 202
            and first.headers["cache-control"] == "no-store"
        )
        assert "synthetic-private-question" not in first.text
        again = await client.post(path, content=body, headers=headers)
        assert again.status_code == 202 and again.json()["deduplicated"]
    assert published[0]["args"] == (first.json()["delivery_id"],)
    assert published[0]["args"] == published[1]["args"]


@pytest.mark.asyncio
async def test_direct_production_entry_cannot_bypass_a_signed_webhook(fixture):
    application = FastAPI()
    application.include_router(executions.router, prefix="/v1/workflows")

    async def database():
        async with fixture.sessions() as db:
            yield db

    application.dependency_overrides[get_db] = database
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        result = await client.post(
            f"/v1/workflows/{fixture.workflow_id}/execute",
            params={"project_id": str(fixture.project_id)},
            json={"execution_mode": "production"},
        )
        assert result.status_code == 403


@pytest.mark.asyncio
async def test_watchdog_clears_the_frozen_body(fixture, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import update
    from app.models.execution import WorkflowExecution
    from app.models.workflow_webhook_delivery import WorkflowWebhookDelivery
    from celery_app import tasks
    from tests.unit.test_webhook_dispatch import queued

    identifier, source = await queued(fixture, monkeypatch)
    now = datetime.now(timezone.utc)
    async with fixture.sessions() as db:
        await db.execute(
            update(WorkflowExecution).values(
                status="running", started_at=now - timedelta(minutes=20)
            )
        )
        await db.commit()
    assert await tasks.sweep_expired_internal_events(now=now) == 1
    async with fixture.sessions() as db:
        run = await db.get(WorkflowExecution, identifier)
        row = await db.get(WorkflowWebhookDelivery, str(source))
        assert run.status == "failed" and row.payload_ciphertext is None


@pytest.mark.asyncio
async def test_manual_cancel_clears_input_before_returning(
    fixture, monkeypatch
):
    from unittest.mock import AsyncMock
    from app.models.workflow_webhook_delivery import WorkflowWebhookDelivery
    from tests.unit.test_webhook_dispatch import queued

    identifier, source = await queued(fixture, monkeypatch)
    monkeypatch.setattr(executions, "revoke_queued_execution", AsyncMock())
    async with fixture.sessions() as db:
        result = await executions.cancel_execution(
            identifier, str(fixture.project_id), db
        )
        row = await db.get(WorkflowWebhookDelivery, str(source))
        assert result.status == "cancelled" and row.payload_ciphertext is None
