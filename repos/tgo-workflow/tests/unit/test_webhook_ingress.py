"""Signed notifications are frozen once, independently of broker delivery."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
from unittest.mock import AsyncMock
from uuid import UUID

from fastapi import HTTPException
from pydantic import SecretStr
import pytest
import pytest_asyncio
from sqlalchemy import func, select, update

from app.config import settings
from app.models.execution import WorkflowExecution, NodeExecution
from app.models.approval import WorkflowApprovalRequest
from app.models.workflow import Workflow
from app.models.workflow_release import WorkflowRelease
from app.models.workflow_webhook import WorkflowWebhook
from app.models.workflow_webhook_delivery import WorkflowWebhookDelivery
from app.services import webhook_ingress as ingress
from app.services import workflow_webhooks as hooks
from app.services.execution_snapshot import definition_digest
from tests.unit.test_workflow_webhooks import (
    Fixture,
    request,
    webhook_database,
)


@dataclass
class DeliveryFixture(Fixture):
    hook_id: UUID
    key_id: str
    secret: SecretStr


@pytest_asyncio.fixture
async def fixture(monkeypatch):
    monkeypatch.setattr(
        settings, "WEBHOOK_EXECUTION_ENABLED", True, raising=False
    )
    monkeypatch.setattr(
        settings,
        "WORKFLOW_WEBHOOK_KEYS",
        SecretStr(
            json.dumps(
                {
                    "first": "synthetic-webhook-ingress-master-key-1234567890",
                }
            )
        ),
    )
    monkeypatch.setattr(settings, "WORKFLOW_WEBHOOK_ACTIVE_KEY_ID", "first")
    monkeypatch.setattr(ingress, "verify_webhook_authority", AsyncMock())
    monkeypatch.setattr(ingress, "admit_webhook", AsyncMock())
    monkeypatch.setattr(
        hooks, "verify_webhook_authority", AsyncMock(), raising=False
    )
    async with webhook_database() as base:
        async with base.sessions() as db:
            engine = db.bind
            workflow = await db.get(Workflow, base.workflow_id)
            graph = dict(workflow.definition)
            graph["nodes"][1]["data"][
                "output_template"
            ] = "{{source.body.question}}"
            base.digest = definition_digest(graph)
            workflow.definition = graph
            release = await db.get(WorkflowRelease, base.release_id)
            release.definition, release.definition_digest = graph, base.digest
            await db.commit()
        async with engine.begin() as connection:
            from app.database import Base

            await connection.run_sync(
                lambda db: Base.metadata.create_all(
                    db,
                    tables=[
                        WorkflowWebhookDelivery.__table__,
                        WorkflowExecution.__table__,
                        NodeExecution.__table__,
                        WorkflowApprovalRequest.__table__,
                    ],
                )
            )
        async with base.sessions() as db:
            created = await hooks.save_webhook(
                db,
                base.workflow_id,
                base.project_id,
                base.actor_id,
                request(base),
            )
            await hooks.save_webhook(
                db,
                base.workflow_id,
                base.project_id,
                base.actor_id,
                request(base, revision=1, enabled=True),
            )
        yield DeliveryFixture(
            **base.__dict__,
            hook_id=created.webhook.id,
            key_id=created.webhook.key_id,
            secret=created.credential.secret,
        )


def signed(fixture, data=None, event_id="synthetic-event-001", **changes):
    body = json.dumps(
        {"data": data or {"question": "synthetic-private-question"}},
        separators=(",", ":"),
    ).encode()
    stamp = str(int(datetime.now(timezone.utc).timestamp()))
    key_id = changes.get("key_id", fixture.key_id)
    message = (
        f"TGO-WEBHOOK-V1\n{fixture.hook_id}\n1\n{key_id}\n"
        f"{stamp}\n{event_id}\n"
    ).encode() + body
    signature = hmac.new(
        fixture.secret.get_secret_value().encode(), message, hashlib.sha256
    ).hexdigest()
    headers = dict(
        timestamp=stamp,
        event_id=event_id,
        key_id=key_id,
        signature="sha256=" + signature,
    )
    return body, headers


async def accept(
    fixture, data=None, event_id="synthetic-event-001", **changes
):
    body, headers = signed(fixture, data, event_id)
    headers.update(changes)
    async with fixture.sessions() as db:
        return await ingress.accept_delivery(
            db,
            fixture.hook_id,
            body=body,
            headers=headers,
            content_type="application/json",
        )


@pytest.mark.asyncio
async def test_accept_is_encrypted_durable_and_replays_one_source(fixture):
    first = await accept(fixture)
    again = await accept(fixture)
    assert first.delivery_id == again.delivery_id
    assert first.status == "pending" and first.execution_id is None
    assert not first.deduplicated and again.deduplicated
    async with fixture.sessions() as db:
        row = await db.get(WorkflowWebhookDelivery, str(first.delivery_id))
        assert "synthetic-private-question" not in row.payload_ciphertext
        assert "synthetic-event-001" not in row.event_key
        assert (
            await db.scalar(
                select(func.count()).select_from(WorkflowWebhookDelivery)
            )
            == 1
        )
        assert (
            await db.scalar(
                select(func.count()).select_from(WorkflowExecution)
            )
            == 0
        )


@pytest.mark.asyncio
async def test_changed_data_cannot_reuse_an_event_id(fixture):
    await accept(fixture)
    with pytest.raises(HTTPException) as denied:
        await accept(fixture, {"question": "different"})
    assert denied.value.status_code == 409


@pytest.mark.asyncio
async def test_bad_signature_cannot_touch_rate_admission_or_storage(
    fixture, monkeypatch
):
    admit = AsyncMock()
    monkeypatch.setattr(ingress, "admit_webhook", admit)
    with pytest.raises(HTTPException) as denied:
        await accept(fixture, signature="sha256=" + "0" * 64)
    assert denied.value.status_code == 403
    admit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,status", [(PermissionError, 403), (RuntimeError, 503)]
)
async def test_current_permission_failure_is_not_acknowledged(
    fixture, monkeypatch, error, status
):
    monkeypatch.setattr(
        ingress, "verify_webhook_authority", AsyncMock(side_effect=error())
    )
    with pytest.raises(HTTPException) as denied:
        await accept(fixture)
    assert denied.value.status_code == status
    async with fixture.sessions() as db:
        assert (
            await db.scalar(
                select(func.count()).select_from(WorkflowWebhookDelivery)
            )
            == 0
        )


@pytest.mark.asyncio
async def test_disable_during_permission_wait_fences_late_reply(
    fixture, monkeypatch
):
    async def disable(query):
        async with fixture.sessions() as db:
            await db.execute(
                update(WorkflowWebhook).values(enabled=False, revision=3)
            )
            await db.commit()

    monkeypatch.setattr(ingress, "verify_webhook_authority", disable)
    with pytest.raises(HTTPException) as denied:
        await accept(fixture)
    assert denied.value.status_code == 409


@pytest.mark.asyncio
async def test_stale_release_and_disabled_gate_cannot_accept(
    fixture, monkeypatch
):
    async with fixture.sessions() as db:
        await db.execute(update(Workflow).values(published_version=2))
        await db.commit()
    with pytest.raises(HTTPException) as stale:
        await accept(fixture)
    assert stale.value.status_code == 409
    monkeypatch.setattr(settings, "WEBHOOK_EXECUTION_ENABLED", False)
    with pytest.raises(HTTPException) as disabled:
        await accept(fixture)
    assert disabled.value.status_code == 503


@pytest.mark.asyncio
async def test_rate_unavailable_or_denied_never_creates_work(
    fixture, monkeypatch
):
    monkeypatch.setattr(
        ingress,
        "admit_webhook",
        AsyncMock(side_effect=HTTPException(429, "limited")),
    )
    with pytest.raises(HTTPException) as denied:
        await accept(fixture)
    assert denied.value.status_code == 429


@pytest.mark.asyncio
async def test_missing_historical_key_cannot_turn_a_duplicate_into_new_work(
    fixture, monkeypatch
):
    await accept(fixture)
    monkeypatch.setattr(
        settings,
        "WORKFLOW_WEBHOOK_KEYS",
        SecretStr(
            json.dumps(
                {
                    "second": "synthetic-second-ingress-master-key-1234567890",
                }
            )
        ),
    )
    monkeypatch.setattr(settings, "WORKFLOW_WEBHOOK_ACTIVE_KEY_ID", "second")
    # Re-seal the signing credential while historical input keys stay missing.
    from app.services.webhook_crypto import seal_credential

    async with fixture.sessions() as db:
        row = await db.get(WorkflowWebhook, str(fixture.hook_id))
        row.credential_ciphertext = seal_credential(
            hook_id=fixture.hook_id,
            credential_revision=1,
            key_id=fixture.key_id,
            secret=fixture.secret,
        )
        await db.commit()
    with pytest.raises(HTTPException) as denied:
        await accept(fixture)
    assert denied.value.status_code == 503
