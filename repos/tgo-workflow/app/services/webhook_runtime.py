"""Current source checks at every node boundary and confidential input load."""

from datetime import datetime, timezone
from typing import Literal, NoReturn
from pydantic import JsonValue
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.integrations.webhook_authority_client import verify_webhook_authority
from app.models.execution import WorkflowExecution
from app.models.workflow_webhook_delivery import WorkflowWebhookDelivery
from app.services.execution_lifecycle import ExecutionStopped
from app.services.approval_retention import scrub_terminal_approval_checkpoints
from app.services.webhook_crypto import WebhookCryptoError
from app.services.webhook_lifecycle import STOP_MESSAGE
from app.services.webhook_payloads import open_payload, matches_payload
from app.services.webhook_state import (
    authority,
    binding_error,
    execution_id,
    lock_delivery,
    payload_scope,
    utc,
)

WebhookRunState = Literal["pending", "running", "waiting_approval", "pending_resume"]


class WebhookSourceStopped(ExecutionStopped):
    def __init__(self, message: str, *, changed: bool):
        super().__init__(message)
        self.changed = changed


class WebhookSourceUnavailable(RuntimeError):
    """A waiting-source audit will retry authority without opening its body."""


async def _stop(
    db: AsyncSession,
    run: WorkflowExecution,
    *,
    failed: bool = False,
    expected_status: WebhookRunState = "running",
    error_code: str | None = None,
) -> NoReturn:
    changed = await db.scalar(
        update(WorkflowExecution)
        .where(
            WorkflowExecution.id == run.id,
            WorkflowExecution.project_id == run.project_id,
            WorkflowExecution.workflow_id == run.workflow_id,
            WorkflowExecution.source_channel == "external_webhook",
            WorkflowExecution.status == expected_status,
        )
        .returning(WorkflowExecution.id)
        .values(
            status="failed" if failed else "cancelled",
            input=None,
            output=None,
            error=STOP_MESSAGE,
            completed_at=datetime.now(timezone.utc),
        )
    )
    from app.services.webhook_lifecycle import scrub_webhook_inputs

    if changed is not None:
        if error_code is not None:
            await db.execute(
                update(WorkflowWebhookDelivery)
                .where(
                    WorkflowWebhookDelivery.execution_id == run.id,
                    WorkflowWebhookDelivery.project_id == run.project_id,
                    WorkflowWebhookDelivery.status == "queued",
                )
                .values(error_code=error_code)
            )
        await scrub_terminal_approval_checkpoints(db, run.project_id, run.id)
        await scrub_webhook_inputs(db, run.project_id, run.id)
    await db.commit()
    raise WebhookSourceStopped(STOP_MESSAGE, changed=changed is not None)


async def require_active_webhook(
    db: AsyncSession,
    project_id: str,
    workflow_id: str,
    identifier: str,
    *,
    retain_lock: bool = False,
    expected_status: WebhookRunState = "running",
    defer_unavailable: bool = False,
) -> None:
    run = await db.get(WorkflowExecution, identifier, populate_existing=True)
    if (
        run is None
        or run.project_id != project_id
        or run.workflow_id != workflow_id
        or run.status != expected_status
    ):
        raise ExecutionStopped(STOP_MESSAGE)
    row = await db.scalar(
        select(WorkflowWebhookDelivery).where(
            WorkflowWebhookDelivery.execution_id == identifier,
            WorkflowWebhookDelivery.project_id == project_id,
            WorkflowWebhookDelivery.workflow_id == workflow_id,
        )
    )
    if (
        not settings.WEBHOOK_EXECUTION_ENABLED
        or row is None
        or row.status != "queued"
        or identifier != execution_id(row.id)
        or run.source_channel != "external_webhook"
        or run.execution_mode != "production"
        or run.release_id != row.release_id
        or run.definition_digest != row.release_digest
        or run.source_message_id != "webhook-" + row.id
        or run.input != {"_webhook_delivery_id": row.id}
    ):
        await _stop(db, run, expected_status=expected_status)
    query = authority(row)
    await db.commit()
    denial: str | None = None
    try:
        await verify_webhook_authority(query)
    except PermissionError:
        denial = "webhook_authority_revoked"
    except RuntimeError:
        denial = "webhook_authority_unavailable"
    row = await lock_delivery(db, str(row.id))
    run = await db.get(WorkflowExecution, identifier, populate_existing=True)
    if run is None or run.status != expected_status:
        await db.rollback()
        raise ExecutionStopped(STOP_MESSAGE)
    if row is None or row.status != "queued":
        await _stop(db, run, expected_status=expected_status)
    current = datetime.now(timezone.utc)
    error = await binding_error(db, row)
    expired = row.claimed_at is None and utc(row.expires_at) <= current
    if (
        defer_unavailable
        and denial == "webhook_authority_unavailable"
        and not error
        and settings.WEBHOOK_EXECUTION_ENABLED
        and not expired
    ):
        await db.rollback()
        raise WebhookSourceUnavailable("外部通知来源暂时无法核验，保留等待下次巡检。")
    if denial or error or not settings.WEBHOOK_EXECUTION_ENABLED or expired:
        await _stop(
            db,
            run,
            failed=denial == "webhook_authority_unavailable",
            expected_status=expected_status,
            error_code=denial or error or "webhook_expired",
        )
    if row.claimed_at is None and expected_status == "running":
        row.claimed_at = current
    if not retain_lock:
        await db.commit()


async def webhook_inputs(
    db: AsyncSession,
    project_id: str,
    workflow_id: str,
    identifier: str,
) -> dict[str, JsonValue]:
    await require_active_webhook(db, project_id, workflow_id, identifier)
    run = await db.get(WorkflowExecution, identifier, populate_existing=True)
    if run is None:
        raise ExecutionStopped(STOP_MESSAGE)
    try:
        inputs = await frozen_webhook_inputs(db, run)
    except WebhookCryptoError:
        await _stop(db, run, failed=True)
    await db.commit()
    return inputs


async def frozen_webhook_inputs(
    db: AsyncSession,
    run: WorkflowExecution,
) -> dict[str, JsonValue]:
    """Read the fixed payload for a proof, without authorizing execution."""
    row = await db.scalar(
        select(WorkflowWebhookDelivery).where(
            WorkflowWebhookDelivery.execution_id == run.id,
            WorkflowWebhookDelivery.project_id == run.project_id,
            WorkflowWebhookDelivery.workflow_id == run.workflow_id,
        )
    )
    if (
        row is None
        or row.status != "queued"
        or row.payload_ciphertext is None
        or run.id != execution_id(row.id)
        or run.source_channel != "external_webhook"
        or run.execution_mode != "production"
        or run.release_id != row.release_id
        or run.definition_digest != row.release_digest
        or run.source_message_id != "webhook-" + row.id
        or run.input != {"_webhook_delivery_id": row.id}
    ):
        raise WebhookCryptoError("外部通知冻结输入归属不一致。")
    scope = payload_scope(row)
    data = open_payload(scope, row.payload_ciphertext)
    if not matches_payload(
        scope.hook_id,
        scope.event_key,
        data,
        row.payload_key_id,
        row.payload_receipt,
    ):
        raise WebhookCryptoError("外部通知冻结输入无法核验。")
    return {"data": data}
