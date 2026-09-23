"""Read-only commercial backlog signals for local operational monitoring."""

from datetime import datetime, timedelta, timezone
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from app.models.ai_usage import AIUsageReservation
from app.models.billing import BillingJob, BillingOrder
from app.models.billing_reconciliation import BillingReconciliation
from app.schemas.commercial_health import CommercialHealthReport


def inspect_commercial_health(
    db: Session, *, now: datetime | None = None
) -> CommercialHealthReport:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("An aware timestamp is required")
    grace = now - timedelta(minutes=5)
    checks: dict[str, Select[tuple[int]]] = {
        "paid_unfulfilled": select(func.count())
        .select_from(BillingOrder)
        .where(
            BillingOrder.payment_status == "paid",
            BillingOrder.fulfillment_status == "pending",
            BillingOrder.paid_at <= grace,
        ),
        "fulfillment_conflicts": select(func.count())
        .select_from(BillingOrder)
        .where(
            BillingOrder.payment_status == "paid",
            BillingOrder.fulfillment_status == "conflict",
        ),
        "overdue_jobs": select(func.count())
        .select_from(BillingJob)
        .where(
            BillingJob.status == "pending",
            BillingJob.available_at <= now - timedelta(minutes=30),
            or_(BillingJob.locked_until.is_(None), BillingJob.locked_until <= now),
        ),
        "expired_job_leases": select(func.count())
        .select_from(BillingJob)
        .where(
            BillingJob.status == "pending",
            BillingJob.locked_until <= now,
        ),
        "review_jobs": select(func.count())
        .select_from(BillingJob)
        .where(
            BillingJob.status.in_(["review", "failed"]),
        ),
        "stale_ai_reservations": select(func.count())
        .select_from(AIUsageReservation)
        .where(
            AIUsageReservation.status.in_(["reserved", "publishing"]),
            AIUsageReservation.expires_at <= grace,
        ),
        "review_ai_reservations": select(func.count())
        .select_from(AIUsageReservation)
        .where(
            AIUsageReservation.status == "review",
        ),
        "statement_differences": select(
            func.coalesce(func.sum(BillingReconciliation.issue_count), 0)
        ).where(BillingReconciliation.status == "review"),
    }
    with db.no_autoflush:
        counts = {name: int(db.scalar(query) or 0) for name, query in checks.items()}
    return CommercialHealthReport(
        checked_at=now,
        counts=counts,
        status="attention" if any(counts.values()) else "clear",
    )
