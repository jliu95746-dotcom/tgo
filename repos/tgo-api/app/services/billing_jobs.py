"""Recoverable database leases for paid-order fulfillment."""

from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import or_, select

from app.core.database import SessionLocal
from app.models.billing import BillingJob
from app.services.billing_fulfillment import fulfill_order


def run_one() -> bool:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        job = db.scalar(
            select(BillingJob)
            .where(
                BillingJob.kind.in_(["fulfill", "refund", "tradebill"]),
                BillingJob.status == "pending",
                BillingJob.available_at <= now,
                or_(BillingJob.locked_until.is_(None), BillingJob.locked_until <= now),
            )
            .order_by(BillingJob.available_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return False
        job.lease_id = uuid4()
        job.locked_until = now + timedelta(minutes=5)
        job.attempts += 1
        job_id, order_id, lease = job.id, job.order_id, job.lease_id
        kind, business_key = job.kind, job.business_key
        db.commit()
    error: str | None = None
    state = "done"
    try:
        if kind == "tradebill":
            from app.services.billing_reconciliation import reconcile_statement

            state = reconcile_statement(
                date.fromisoformat(business_key.removeprefix("tradebill:"))
            )
        elif kind == "refund":
            from app.services.billing_refund_jobs import process_refund

            state = process_refund(UUID(business_key.removeprefix("refund:")))
        else:
            if order_id is None:
                raise ValueError("Missing fulfillment order")
            with SessionLocal() as db:
                order = fulfill_order(db, order_id)
                state = "review" if order.fulfillment_status == "conflict" else "done"
                db.commit()
    except Exception as exc:
        error = type(exc).__name__
    with SessionLocal() as db:
        job = db.scalar(
            select(BillingJob)
            .where(BillingJob.id == job_id, BillingJob.lease_id == lease)
            .with_for_update()
        )
        if job is not None:
            job.locked_until = None
            job.lease_id = None
            job.last_error = error
            if error is None:
                job.status = state
                if state == "pending":
                    job.available_at = datetime.now(timezone.utc) + timedelta(minutes=1)
            else:
                job.available_at = datetime.now(timezone.utc) + timedelta(
                    seconds=min(3600, 2 ** min(job.attempts, 11))
                )
            db.commit()
    return True
