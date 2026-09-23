"""Retry scheduling leaves fulfillment, payment and review invariants intact."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.billing import BillingAudit, BillingJob
from app.models.platform_operator import PlatformOperator
from app.schemas.operations_tasks import RetryTask
from app.services.billing_quotes import conflict
from app.services.company_email import utc


def retry_task(
    db: Session, operator: PlatformOperator, identifier: UUID, payload: RetryTask
) -> BillingJob:
    job = db.scalar(
        select(BillingJob).where(BillingJob.id == identifier).with_for_update()
    )
    now = datetime.now(timezone.utc)
    if job is None or job.status != "pending" or not job.last_error:
        raise conflict("仅失败后等待重试的任务可以加速重试，待复核订单须先处理异常")
    if job.locked_until and utc(job.locked_until) > now:
        raise conflict("任务正在执行，请等待处理结果")
    job.available_at = now
    db.add(
        BillingAudit(
            operator_id=operator.id,
            project_id=job.project_id,
            action="task_retry",
            reason=payload.reason,
            detail={"task_id": str(job.id), "kind": job.kind, "attempts": job.attempts},
        )
    )
    db.flush()
    return job
