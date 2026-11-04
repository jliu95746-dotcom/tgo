"""Source registration, credential recovery, tenant and version fencing."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
import json
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import SecretStr, ValidationError
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.database import Base
from app.models.workflow import Workflow
from app.models.workflow_release import WorkflowRelease
from app.models.workflow_webhook import WorkflowWebhook, WorkflowWebhookCommand
from app.models.workflow_webhook_delivery import WorkflowWebhookDelivery
from app.schemas.workflow_webhook import WebhookChange, WebhookSave
from app.services import workflow_webhooks as hooks
from app.services.execution_snapshot import definition_digest
from app.services.webhook_crypto import open_credential


@dataclass
class Fixture:
    sessions: async_sessionmaker[AsyncSession]
    project_id: UUID
    actor_id: UUID
    workflow_id: str
    release_id: str
    digest: str


def definition(kind="webhook"):
    return {
        "nodes": [
            {
                "id": "source",
                "type": kind,
                "data": {
                    "type": kind,
                    "reference_key": "source",
                    "method": "POST",
                },
            },
            {
                "id": "answer",
                "type": "answer",
                "data": {
                    "reference_key": "answer",
                    "output_type": "template",
                    "output_template": "合成触发结果",
                },
            },
        ],
        "edges": [{"source": "source", "target": "answer"}],
    }


@asynccontextmanager
async def webhook_database() -> AsyncIterator[Fixture]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda db: Base.metadata.create_all(
                db,
                tables=[
                    Workflow.__table__,
                    WorkflowRelease.__table__,
                    WorkflowWebhook.__table__,
                    WorkflowWebhookCommand.__table__,
                    WorkflowWebhookDelivery.__table__,
                ],
            )
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project, actor = uuid4(), uuid4()
    flow, release = str(uuid4()), str(uuid4())
    graph = definition()
    digest = definition_digest(graph)
    async with sessions() as db:
        db.add(
            Workflow(
                id=flow,
                project_id=str(project),
                name="合成外部触发",
                definition=graph,
                status="active",
                version=1,
                published_version=1,
                publication_revision=1,
            )
        )
        await db.flush()
        db.add(
            WorkflowRelease(
                id=release,
                workflow_id=flow,
                project_id=str(project),
                version=1,
                draft_version=1,
                definition=graph,
                definition_digest=digest,
            )
        )
        await db.commit()
    try:
        yield Fixture(sessions, project, actor, flow, release, digest)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def fixture(monkeypatch) -> AsyncIterator[Fixture]:
    monkeypatch.setattr(
        settings,
        "WORKFLOW_WEBHOOK_KEYS",
        SecretStr(
            json.dumps(
                {
                    "first": "synthetic-registration-master-key-1234567890",
                }
            )
        ),
        raising=False,
    )
    monkeypatch.setattr(
        settings, "WORKFLOW_WEBHOOK_ACTIVE_KEY_ID", "first", raising=False
    )
    async with webhook_database() as result:
        yield result


def request(fixture, revision=0, **changes):
    values = dict(
        request_id=uuid4(),
        expected_revision=revision,
        expected_release_id=fixture.release_id,
        expected_release_digest=fixture.digest,
        enabled=False,
    )
    values.update(changes)
    return WebhookSave(**values)


async def save(fixture, req):
    async with fixture.sessions() as db:
        return await hooks.save_webhook(
            db, fixture.workflow_id, fixture.project_id, fixture.actor_id, req
        )


async def change(fixture, operation, revision):
    req = WebhookChange(request_id=uuid4(), expected_revision=revision)
    async with fixture.sessions() as db:
        return await operation(
            db, fixture.workflow_id, fixture.project_id, fixture.actor_id, req
        )


@pytest.mark.asyncio
async def test_create_is_disabled_encrypted_and_retry_recovers_same_credential(
    fixture,
):
    req = request(fixture)
    first = await save(fixture, req)
    repeated = await save(fixture, req)
    assert not first.replayed and repeated.replayed
    assert first.webhook == repeated.webhook
    assert first.credential == repeated.credential
    assert first.credential_status == "available"
    assert not first.webhook.enabled
    assert first.webhook.revision == first.webhook.credential_revision == 1
    assert first.credential is not None
    secret = first.credential.secret.get_secret_value()
    async with fixture.sessions() as db:
        row = await db.get(WorkflowWebhook, str(first.webhook.id))
        assert row is not None and secret not in row.credential_ciphertext
        assert (
            open_credential(
                hook_id=first.webhook.id,
                credential_revision=1,
                key_id=row.key_id,
                encrypted=row.credential_ciphertext,
            ).get_secret_value()
            == secret
        )
        command = await db.get(
            WorkflowWebhookCommand,
            (str(fixture.project_id), str(req.request_id)),
        )
        assert command is not None
        assert secret not in json.dumps(command.result_snapshot)
        assert "ciphertext" not in json.dumps(command.result_snapshot)
        assert (
            await db.scalar(select(func.count()).select_from(WorkflowWebhook))
            == 1
        )
        state = await hooks.get_webhook(
            db, fixture.workflow_id, fixture.project_id
        )
        assert state.binding_current and state.registration_available
        assert not state.execution_available
        assert secret not in state.model_dump_json()
        assert row.credential_ciphertext not in state.model_dump_json()


@pytest.mark.asyncio
async def test_rebinding_preserves_signing_credential(
    fixture,
):
    first = await save(fixture, request(fixture))
    second = await save(fixture, request(fixture, revision=1))
    assert second.webhook.revision == 2
    assert second.webhook.credential_revision == 1
    assert second.webhook.key_id == first.webhook.key_id
    assert (
        second.credential is None and second.credential_status == "not_issued"
    )


@pytest.mark.asyncio
async def test_rotation_replaces_credential_and_original_replay_is_superseded(
    fixture,
):
    req = request(fixture)
    first = await save(fixture, req)
    rotated = await change(fixture, hooks.rotate_webhook, 1)
    assert rotated.webhook.revision == rotated.webhook.credential_revision == 2
    assert rotated.webhook.key_id != first.webhook.key_id
    assert rotated.credential != first.credential
    assert not rotated.webhook.enabled
    old = await save(fixture, req)
    assert old.replayed and old.credential is None
    assert old.credential_status == "superseded"
    async with fixture.sessions() as db:
        current = await hooks.get_command_receipt(
            db,
            fixture.workflow_id,
            fixture.project_id,
            fixture.actor_id,
            rotated.request_id,
        )
        assert current.replayed and current.credential == rotated.credential


@pytest.mark.asyncio
async def test_disable_still_works_when_crypto_configuration_is_missing(
    fixture,
    monkeypatch,
):
    first = await save(fixture, request(fixture))
    monkeypatch.setattr(settings, "WORKFLOW_WEBHOOK_KEYS", None)
    monkeypatch.setattr(settings, "WORKFLOW_WEBHOOK_ACTIVE_KEY_ID", None)
    disabled = await change(fixture, hooks.disable_webhook, 1)
    assert disabled.webhook.revision == 2 and not disabled.webhook.enabled
    assert disabled.webhook.key_id == first.webhook.key_id
    async with fixture.sessions() as db:
        state = await hooks.get_webhook(
            db, fixture.workflow_id, fixture.project_id
        )
        assert not state.registration_available


@pytest.mark.asyncio
async def test_lost_master_keeps_command_receipt_without_exposing_a_secret(
    fixture,
    monkeypatch,
):
    first = await save(fixture, request(fixture))
    monkeypatch.setattr(settings, "WORKFLOW_WEBHOOK_KEYS", None)
    async with fixture.sessions() as db:
        result = await hooks.get_command_receipt(
            db,
            fixture.workflow_id,
            fixture.project_id,
            fixture.actor_id,
            first.request_id,
        )
    assert (
        result.credential is None and result.credential_status == "unavailable"
    )
    assert result.webhook == first.webhook


@pytest.mark.asyncio
async def test_stale_revision_cannot_change_configuration(fixture):
    await save(fixture, request(fixture))
    with pytest.raises(HTTPException) as failure:
        await save(fixture, request(fixture))
    assert failure.value.status_code == 409


@pytest.mark.asyncio
async def test_request_id_cannot_be_reused_for_changed_payload_or_actor(
    fixture,
):
    req = request(fixture)
    await save(fixture, req)
    with pytest.raises(HTTPException) as changed:
        await save(fixture, req.model_copy(update={"expected_revision": 1}))
    assert changed.value.status_code == 409
    async with fixture.sessions() as db:
        with pytest.raises(HTTPException) as other:
            await hooks.save_webhook(
                db, fixture.workflow_id, fixture.project_id, uuid4(), req
            )
        assert other.value.status_code == 409
        with pytest.raises(HTTPException) as hidden:
            await hooks.get_command_receipt(
                db,
                fixture.workflow_id,
                fixture.project_id,
                uuid4(),
                req.request_id,
            )
        assert hidden.value.status_code == 404


@pytest.mark.asyncio
async def test_cross_tenant_access_is_hidden(fixture):
    req = request(fixture)
    await save(fixture, req)
    async with fixture.sessions() as db:
        for operation in (hooks.get_webhook, hooks.save_webhook):
            args = [db, fixture.workflow_id, uuid4()]
            if operation is hooks.save_webhook:
                args += [fixture.actor_id, req]
            with pytest.raises(HTTPException) as denied:
                await operation(*args)
            assert denied.value.status_code == 404


@pytest.mark.asyncio
async def test_enabled_registration_is_rejected_until_execution_is_available(
    fixture,
):
    with pytest.raises(HTTPException) as denied:
        await save(fixture, request(fixture, enabled=True))
    assert denied.value.status_code == 409
    async with fixture.sessions() as db:
        assert (
            await db.scalar(select(func.count()).select_from(WorkflowWebhook))
            == 0
        )


@pytest.mark.asyncio
async def test_wrong_release_or_corrupted_fixed_definition_is_rejected(
    fixture,
):
    with pytest.raises(HTTPException) as denied:
        await save(fixture, request(fixture, expected_release_digest="a" * 64))
    assert denied.value.status_code == 409
    async with fixture.sessions() as db:
        release = await db.get(WorkflowRelease, fixture.release_id)
        release.definition = definition("input")
        await db.commit()
    with pytest.raises(HTTPException) as corrupt:
        await save(fixture, request(fixture))
    assert corrupt.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", [("GET", None), ("POST", "/custom")])
async def test_legacy_get_and_custom_paths_cannot_be_registered(
    fixture, method, path
):
    graph = definition()
    graph["nodes"][0]["data"].update(method=method, path=path)
    async with fixture.sessions() as db:
        release = await db.get(WorkflowRelease, fixture.release_id)
        release.definition = graph
        release.definition_digest = definition_digest(graph)
        fixture.digest = release.definition_digest
        await db.commit()
    with pytest.raises(HTTPException) as denied:
        await save(fixture, request(fixture))
    assert denied.value.status_code == 409


@pytest.mark.parametrize(
    "injected", ["project_id", "actor_staff_id", "secret", "key_id"]
)
def test_public_save_does_not_accept_server_owned_fields(injected):
    with pytest.raises(ValidationError):
        WebhookSave.model_validate(
            dict(
                request_id=uuid4(),
                expected_revision=0,
                expected_release_id="release",
                expected_release_digest="a" * 64,
                **{injected: "injected"}
            )
        )
