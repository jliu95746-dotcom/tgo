"""Management views disclose only owned, verified source metadata."""

from uuid import uuid4

from fastapi import HTTPException
import pytest

from app.models.execution import WorkflowExecution
from app.models.workflow_release import WorkflowRelease
from app.models.workflow_webhook_delivery import WorkflowWebhookDelivery
from app.services import webhook_management_views as views
from app.services import workflow_webhooks as hooks
from app.services.webhook_state import execution_id
from tests.unit import test_webhook_ingress as ingress

fixture = ingress.fixture


@pytest.mark.asyncio
async def test_state_reports_current_verified_release_without_credentials(
    fixture,
):
    async with fixture.sessions() as db:
        state = await hooks.get_webhook(
            db, fixture.workflow_id, fixture.project_id
        )
        assert state.current_release.release_id == fixture.release_id
        assert state.current_release.definition_digest == fixture.digest
        assert state.current_release.version == 1
        encoded = state.model_dump_json()
        assert fixture.secret.get_secret_value() not in encoded
        assert "ciphertext" not in encoded
        release = await db.get(WorkflowRelease, fixture.release_id)
        release.definition_digest = "0" * 64
        await db.commit()
        changed = await hooks.get_webhook(
            db, fixture.workflow_id, fixture.project_id
        )
        assert changed.current_release is None
        assert not changed.binding_current


@pytest.mark.asyncio
async def test_history_is_paginated_and_excludes_payload_and_identifiers(
    fixture,
):
    await ingress.accept(fixture, event_id="private-external-event-a")
    await ingress.accept(fixture, event_id="private-external-event-b")
    async with fixture.sessions() as db:
        page = await views.list_deliveries(
            db, fixture.workflow_id, fixture.project_id, 1, 0
        )
        assert page.total == 2 and len(page.items) == 1 and page.has_more
        assert page.workflow_id == fixture.workflow_id
        assert page.project_id == fixture.project_id
        assert page.items[0].execution_id is None
        encoded = page.model_dump_json()
        for private in (
            "private-external-event",
            "synthetic-private-question",
            "payload_ciphertext",
            "event_key",
            "payload_receipt",
        ):
            assert private not in encoded
        second = await views.list_deliveries(
            db, fixture.workflow_id, fixture.project_id, 1, 1
        )
        assert second.items[0].id != page.items[0].id
        assert not second.has_more


@pytest.mark.asyncio
async def test_foreign_project_cannot_list_sources(fixture):
    async with fixture.sessions() as db:
        with pytest.raises(HTTPException) as error:
            await views.list_deliveries(
                db, fixture.workflow_id, uuid4(), 10, 0
            )
        assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_private_history_requires_internal_identity(
    fixture, monkeypatch
):
    from fastapi import FastAPI
    import httpx
    from pydantic import SecretStr
    from app.api import webhooks
    from app.config import settings
    from app.database import get_db

    monkeypatch.setattr(
        settings, "SECRET_KEY", SecretStr("synthetic-view-key")
    )
    app = FastAPI()
    app.include_router(webhooks.router, prefix="/v1/workflows")

    async def session():
        async with fixture.sessions() as db:
            yield db

    app.dependency_overrides[get_db] = session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        path = f"/v1/workflows/{fixture.workflow_id}/webhook/deliveries"
        query = {"project_id": str(fixture.project_id)}
        denied = await client.get(path, params=query)
        assert denied.status_code == 403
        allowed = await client.get(
            path,
            params=query,
            headers={
                "X-Internal-API-Key": "synthetic-view-key",
            },
        )
        assert allowed.status_code == 200
        assert allowed.headers["Cache-Control"] == "no-store"


@pytest.mark.asyncio
@pytest.mark.parametrize("valid", [False, True])
async def test_link_requires_matching_execution_source_and_release(
    fixture, valid
):
    accepted = await ingress.accept(fixture)
    async with fixture.sessions() as db:
        row = await db.get(WorkflowWebhookDelivery, str(accepted.delivery_id))
        identifier = execution_id(row.id)
        db.add(
            WorkflowExecution(
                id=identifier,
                project_id=str(fixture.project_id),
                workflow_id=fixture.workflow_id,
                status="completed",
                release_id=fixture.release_id,
                definition_digest=fixture.digest if valid else "0" * 64,
                execution_mode="production",
                source_channel="external_webhook",
                source_message_id="webhook-" + row.id,
            )
        )
        row.execution_id = identifier
        await db.commit()
        page = await views.list_deliveries(
            db, fixture.workflow_id, fixture.project_id, 10, 0
        )
        assert (page.items[0].execution_id is not None) is valid
        assert page.items[0].execution_status == (
            "completed" if valid else None
        )
