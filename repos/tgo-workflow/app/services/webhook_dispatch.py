"""Lease notifications, recheck current permission, then enqueue IDs."""

from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.integrations.webhook_authority_client import verify_webhook_authority
from app.models.execution import WorkflowExecution
from app.models.workflow_release import WorkflowRelease
from app.services.execution_snapshot import copy_json_object
from app.services.webhook_state import (
    authority,
    binding_error,
    execution_id,
    lock_delivery,
    utc,
)
from app.services.workflow_dispatch import (
    WorkflowInitialDispatchError,
    enqueue_pending_initial,
)


async def dispatch_delivery(
    db: AsyncSession,
    identifier: str,
    *,
    now: datetime | None = None,
) -> str | None:
    current = utc(now or datetime.now(timezone.utc))
    row = await lock_delivery(db, identifier)
    if (
        row is None
        or row.status != "pending"
        or utc(row.next_attempt_at) > current
    ):
        return None
    error = await binding_error(db, row)
    expired = utc(row.expires_at) <= current
    exhausted = row.attempts >= 8
    if error or expired or exhausted or not settings.WEBHOOK_EXECUTION_ENABLED:
        row.status = (
            "expired" if expired else "blocked" if exhausted else "cancelled"
        )
        row.error_code = error or (
            "webhook_expired"
            if expired
            else "webhook_retry_exhausted"
            if exhausted
            else "webhook_gate_disabled"
        )
        row.payload_ciphertext, row.updated_at = None, current
        await db.commit()
        return None
    query = authority(row)
    row.attempts += 1
    attempt = row.attempts
    row.next_attempt_at, row.updated_at = (
        current + timedelta(seconds=30),
        current,
    )
    await db.commit()
    denial: str | None = None
    try:
        await verify_webhook_authority(query)
    except PermissionError:
        denial = "webhook_authority_revoked"
    except RuntimeError:
        denial = "webhook_authority_unavailable"
    finished = utc(now or datetime.now(timezone.utc))
    row = await lock_delivery(db, identifier)
    if row is None or row.status != "pending" or row.attempts != attempt:
        return None
    error = await binding_error(db, row)
    if error or not settings.WEBHOOK_EXECUTION_ENABLED:
        row.status, row.error_code = (
            "cancelled",
            error or "webhook_gate_disabled",
        )
    elif utc(row.expires_at) <= finished:
        row.status, row.error_code = "expired", "webhook_expired"
    elif denial == "webhook_authority_revoked":
        row.status, row.error_code = "blocked", denial
    elif denial:
        row.error_code = denial
        row.next_attempt_at = finished + timedelta(
            seconds=min(120, 5 * 2 ** min(attempt, 5))
        )
    else:
        release = await db.get(WorkflowRelease, row.release_id)
        if release is None:
            row.status, row.error_code = "blocked", "webhook_release_changed"
        else:
            identifier_run = execution_id(row.id)
            run = WorkflowExecution(
                id=identifier_run,
                project_id=row.project_id,
                workflow_id=row.workflow_id,
                status="pending",
                input={"_webhook_delivery_id": row.id},
                definition_snapshot=copy_json_object(release.definition),
                workflow_version=release.version,
                definition_digest=row.release_digest,
                execution_mode="production",
                release_id=row.release_id,
                source_channel="external_webhook",
                source_message_id="webhook-" + row.id,
                workflow_lineage=[row.workflow_id],
                started_at=finished,
            )
            db.add(run)
            row.execution_id, row.status, row.error_code = (
                identifier_run,
                "queued",
                None,
            )
            row.updated_at = finished
            await db.commit()
            try:
                await enqueue_pending_initial(run)
            except WorkflowInitialDispatchError:
                # Pending-run recovery retries the same SQL execution ID.
                pass
            return identifier_run
    if row.status != "pending":
        row.payload_ciphertext = None
    row.updated_at = finished
    await db.commit()
    return None
