"""Reference-only broker kick and bounded durable notification recovery."""

import asyncio
from datetime import datetime, timezone
from sqlalchemy import select

from celery_app.celery import celery_app
from celery_app.task_database import workflow_task_session
from app.models.execution import WorkflowExecution
from app.models.workflow_webhook_delivery import WorkflowWebhookDelivery
from app.services.webhook_dispatch import dispatch_delivery
from app.services.webhook_lifecycle import scrub_webhook_inputs, TERMINAL
from app.services.webhook_state import utc


@celery_app.task(name="dispatch_webhook_delivery", acks_late=True)
def dispatch_webhook_delivery_task(identifier: str) -> str | None:
    async def dispatch() -> str | None:
        async with workflow_task_session() as db:
            return await dispatch_delivery(db, identifier)

    return asyncio.run(dispatch())


@celery_app.task(name="recover_webhook_deliveries", acks_late=True)
def recover_webhook_deliveries_task() -> int:
    return asyncio.run(sweep_webhook_deliveries())


async def sweep_webhook_deliveries(*, now: datetime | None = None) -> int:
    current = now or datetime.now(timezone.utc)
    async with workflow_task_session() as db:
        rows = (
            await db.scalars(
                select(WorkflowWebhookDelivery)
                .where(
                    WorkflowWebhookDelivery.status.in_(("pending", "queued")),
                    WorkflowWebhookDelivery.next_attempt_at <= current,
                )
                .order_by(
                    WorkflowWebhookDelivery.next_attempt_at,
                    WorkflowWebhookDelivery.id,
                )
                .limit(100)
            )
        ).all()
        count = 0
        for row in rows:
            if row.status == "pending":
                if await dispatch_delivery(db, row.id, now=current):
                    count += 1
            elif row.execution_id:
                run = await db.get(
                    WorkflowExecution, row.execution_id, populate_existing=True
                )
                if run is not None and run.status in TERMINAL:
                    await scrub_webhook_inputs(
                        db, row.project_id, row.execution_id
                    )
                elif (
                    run is not None
                    and run.status == "pending"
                    and utc(row.expires_at) <= current
                ):
                    run.status, run.input, run.output = "cancelled", None, None
                    run.completed_at, run.error = current, "外部通知等待执行已超时。"
                    await db.flush()
                    await scrub_webhook_inputs(
                        db, row.project_id, row.execution_id
                    )
                await db.commit()
        return count
