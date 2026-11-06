"""Reconcile abandoned approved claims without replaying started actions."""

import asyncio
from datetime import datetime, timezone

from app.core.logging import logger
from app.models.execution import WorkflowExecution
from app.services.approval_resume_recovery import recover_interrupted_approval
from app.services.event_execution_expiry import event_execution_cutoff
from sqlalchemy import select

from celery_app.celery import celery_app
from celery_app.task_database import workflow_task_session


@celery_app.task(
    name="recover_interrupted_approval_resumes",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def recover_interrupted_approval_resumes_task(self) -> int:
    try:
        return asyncio.run(sweep_interrupted_approval_resumes())
    except Exception as error:
        logger.error("Approval claim recovery failed: %s", type(error).__name__)
        raise self.retry(
            exc=RuntimeError("审批恢复巡检暂未完成，请检查后台服务。")
        ) from None


async def sweep_interrupted_approval_resumes(
    *,
    now: datetime | None = None,
) -> int:
    from celery_app.tasks import execute_workflow_task

    hard_limit = execute_workflow_task.time_limit
    if hard_limit is None:
        hard_limit = celery_app.conf.task_time_limit
    current = now or datetime.now(timezone.utc)
    cutoff = event_execution_cutoff(current, hard_limit)
    if cutoff is None:
        raise RuntimeError("工作流硬时限无效，不能自动恢复旧执行。")
    async with workflow_task_session() as db:
        rows = (
            await db.execute(
                select(
                    WorkflowExecution.id,
                    WorkflowExecution.project_id,
                )
                .where(
                    WorkflowExecution.status == "running",
                    WorkflowExecution.active_approval_id.is_not(None),
                    WorkflowExecution.active_segment_started_at <= cutoff,
                )
                .order_by(
                    WorkflowExecution.active_segment_started_at,
                    WorkflowExecution.id,
                )
                .limit(100)
            )
        ).all()
        recovered = 0
        for execution_id, project_id in rows:
            result = await recover_interrupted_approval(
                db,
                project_id,
                execution_id,
                cutoff=cutoff,
            )
            recovered += result == "requeued"
        return recovered
