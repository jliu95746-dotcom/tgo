"""Current hook/release fences and shared source identity construction."""

from datetime import datetime, timedelta, timezone
import hashlib
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflow import Workflow
from app.models.workflow_release import WorkflowRelease
from app.models.workflow_webhook import WorkflowWebhook
from app.models.workflow_webhook_delivery import WorkflowWebhookDelivery
from app.schemas.webhook_authority import WebhookAuthority
from app.schemas.webhook_delivery import WebhookPayloadScope
from app.services.execution_snapshot import definition_digest
from app.services.validation_service import ValidationService

START_WINDOW = timedelta(minutes=30)


def utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def event_key(hook_id: UUID, event_id: str) -> str:
    return hashlib.sha256(
        hook_id.bytes + b"\x00" + event_id.encode("ascii")
    ).hexdigest()


def execution_id(identifier: str) -> str:
    return str(uuid5(NAMESPACE_URL, "tgo-webhook-run:v1:" + identifier))


def payload_scope(row: WorkflowWebhookDelivery) -> WebhookPayloadScope:
    return WebhookPayloadScope(
        delivery_id=UUID(row.id),
        hook_id=UUID(row.hook_id),
        project_id=UUID(row.project_id),
        workflow_id=row.workflow_id,
        actor_staff_id=UUID(row.actor_staff_id),
        binding_revision=row.binding_revision,
        credential_revision=row.credential_revision,
        event_key=row.event_key,
        release_id=row.release_id,
        release_digest=row.release_digest,
    )


def authority(
    row: WorkflowWebhook | WorkflowWebhookDelivery,
) -> WebhookAuthority:
    return WebhookAuthority(
        project_id=UUID(row.project_id),
        actor_staff_id=UUID(row.actor_staff_id),
        workflow_id=row.workflow_id,
        hook_id=UUID(
            row.id if isinstance(row, WorkflowWebhook) else row.hook_id
        ),
        binding_revision=row.revision
        if isinstance(row, WorkflowWebhook)
        else row.binding_revision,
        release_id=row.release_id,
        release_digest=row.release_digest,
    )


async def lock_hook(
    db: AsyncSession, identifier: str
) -> WorkflowWebhook | None:
    hint = await db.get(WorkflowWebhook, identifier)
    if hint is None:
        return None
    workflow = await db.scalar(
        select(Workflow)
        .where(
            Workflow.id == hint.workflow_id,
            Workflow.project_id == hint.project_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if workflow is None:
        return None
    return await db.get(WorkflowWebhook, identifier, populate_existing=True)


async def lock_delivery(
    db: AsyncSession, identifier: str
) -> WorkflowWebhookDelivery | None:
    hint = await db.get(WorkflowWebhookDelivery, identifier)
    if hint is None or await lock_hook(db, hint.hook_id) is None:
        return None
    return await db.get(
        WorkflowWebhookDelivery, identifier, populate_existing=True
    )


async def binding_error(
    db: AsyncSession,
    row: WorkflowWebhook | WorkflowWebhookDelivery,
) -> str | None:
    hook = (
        row
        if isinstance(row, WorkflowWebhook)
        else await db.get(WorkflowWebhook, row.hook_id, populate_existing=True)
    )
    workflow = await db.get(Workflow, row.workflow_id, populate_existing=True)
    if (
        hook is None
        or workflow is None
        or hook.project_id != row.project_id
        or hook.workflow_id != row.workflow_id
        or workflow.project_id != row.project_id
    ):
        return "webhook_owner_changed"
    if not hook.enabled:
        return "webhook_disabled"
    if isinstance(row, WorkflowWebhookDelivery) and (
        row.binding_revision != hook.revision
        or row.credential_revision != hook.credential_revision
        or row.actor_staff_id != hook.actor_staff_id
    ):
        return "webhook_revision_changed"
    release = await db.get(
        WorkflowRelease, row.release_id, populate_existing=True
    )
    if (
        workflow.status != "active"
        or release is None
        or release.project_id != row.project_id
        or release.workflow_id != row.workflow_id
        or workflow.published_version != release.version
        or hook.release_id != row.release_id
        or hook.release_digest != row.release_digest
        or release.definition_digest != row.release_digest
    ):
        return "webhook_release_changed"
    try:
        if definition_digest(
            release.definition
        ) != row.release_digest or ValidationService.validate_workflow(
            release.definition, allow_webhook_preparation=True
        ):
            return "webhook_definition_invalid"
        nodes = release.definition.get("nodes")
        if (
            not isinstance(nodes, list)
            or sum(
                isinstance(node, dict) and node.get("type") == "webhook"
                for node in nodes
            )
            != 1
        ):
            return "webhook_definition_invalid"
    except (TypeError, ValueError, KeyError):
        return "webhook_definition_invalid"
    return None
