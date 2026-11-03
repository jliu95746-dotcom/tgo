"""Clear frozen input and fence superseded notifications in one transaction."""

from datetime import datetime, timezone
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.execution import WorkflowExecution
from app.models.workflow_webhook import WorkflowWebhook
from app.models.workflow_webhook_delivery import WorkflowWebhookDelivery
from app.services.approval_retention import scrub_terminal_approval_checkpoints

STOP_MESSAGE = "外部通知来源已停用、变更或失去授权，任务已停止。"

TERMINAL = frozenset({
    "completed", "failed", "cancelled", "rejected", "expired",
})


async def scrub_webhook_inputs(
    db: AsyncSession, project_id: str, execution_id: str
) -> None:
    run = await db.get(WorkflowExecution, execution_id, populate_existing=True)
    if (
        run is None
        or run.project_id != project_id
        or run.source_channel != "external_webhook"
        or run.status not in TERMINAL
    ):
        return
    await db.execute(
        update(WorkflowWebhookDelivery)
        .where(
            WorkflowWebhookDelivery.project_id == project_id,
            WorkflowWebhookDelivery.execution_id == execution_id,
            WorkflowWebhookDelivery.status == "queued",
        )
        .values(
            status="cancelled" if run.status == "rejected" else run.status,
            payload_ciphertext=None,
            updated_at=datetime.now(timezone.utc),
        )
    )


async def cancel_superseded(
    db: AsyncSession, hook: WorkflowWebhook, now: datetime
) -> None:
    rows = (
        await db.scalars(
            select(WorkflowWebhookDelivery).where(
                WorkflowWebhookDelivery.hook_id == hook.id,
                WorkflowWebhookDelivery.project_id == hook.project_id,
                WorkflowWebhookDelivery.binding_revision < hook.revision,
                WorkflowWebhookDelivery.status.in_(("pending", "queued")),
            )
        )
    ).all()
    for row in rows:
        if row.execution_id:
            run = await db.get(
                WorkflowExecution, row.execution_id, populate_existing=True
            )
            if run is not None and run.status in TERMINAL:
                await scrub_terminal_approval_checkpoints(
                    db, hook.project_id, run.id,
                )
                await scrub_webhook_inputs(db, hook.project_id, run.id)
                continue
            changed = await db.scalar(
                update(WorkflowExecution)
                .where(
                    WorkflowExecution.id == row.execution_id,
                    WorkflowExecution.project_id == hook.project_id,
                    WorkflowExecution.source_channel == "external_webhook",
                    WorkflowExecution.status.in_((
                        "pending", "running", "waiting_approval",
                        "pending_resume",
                    )),
                )
                .values(
                    status="cancelled",
                    input=None,
                    output=None,
                    error=STOP_MESSAGE,
                    completed_at=now,
                )
                .returning(WorkflowExecution.id)
            )
            if changed is not None:
                await scrub_terminal_approval_checkpoints(
                    db, hook.project_id, changed,
                )
        row.status, row.error_code = "cancelled", "webhook_revision_changed"
        row.payload_ciphertext, row.updated_at = None, now
