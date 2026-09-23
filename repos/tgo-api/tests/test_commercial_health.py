"""Operational checks flag stalled work without changing customer balances."""

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.ai_usage import AIUsageReservation
from app.models.billing import BillingJob, BillingOrder
from app.models.billing_reconciliation import BillingReconciliation
from app.services.commercial_health import inspect_commercial_health


def test_monitor_detects_stalls_and_ignores_current_or_finished_work():
    engine = create_engine("sqlite://")
    for model in (BillingOrder, BillingJob, AIUsageReservation, BillingReconciliation):
        model.__table__.create(engine)
    now = datetime(2026, 9, 14, tzinfo=timezone.utc)
    with Session(engine) as db:
        for payment, fulfillment, minutes in (
            ("paid", "pending", 6),
            ("paid", "pending", 1),
            ("paid", "applied", 20),
            ("pending", "pending", 20),
            ("paid", "conflict", 1),
        ):
            db.add(
                BillingOrder(
                    project_id=uuid4(),
                    quote_id=uuid4(),
                    amount=100,
                    payment_status=payment,
                    fulfillment_status=fulfillment,
                    expires_at=now,
                    paid_at=now - timedelta(minutes=minutes),
                )
            )
        for status, due, lock in (
            ("pending", -31, None),
            ("pending", -2, None),
            ("pending", -31, 1),
            ("pending", -2, -1),
            ("done", -31, None),
            ("review", -1, None),
        ):
            db.add(
                BillingJob(
                    business_key=str(uuid4()),
                    kind="fulfill",
                    status=status,
                    available_at=now + timedelta(minutes=due),
                    locked_until=None
                    if lock is None
                    else now + timedelta(minutes=lock),
                )
            )
        for status, minutes in (
            ("reserved", -6),
            ("publishing", -6),
            ("reserved", -1),
            ("settled", -6),
            ("released", -6),
            ("review", 1),
        ):
            db.add(
                AIUsageReservation(
                    project_id=uuid4(),
                    batch_id=uuid4(),
                    round_key=str(uuid4()),
                    status=status,
                    expires_at=now + timedelta(minutes=minutes),
                )
            )
        db.add(
            BillingReconciliation(
                bill_date=date(2026, 9, 13),
                status="review",
                source_hash="synthetic",
                entry_count=2,
                issue_count=2,
                issues=[],
            )
        )
        db.commit()
        report = inspect_commercial_health(db, now=now)
        assert report.counts == {
            "paid_unfulfilled": 1,
            "fulfillment_conflicts": 1,
            "overdue_jobs": 1,
            "expired_job_leases": 1,
            "review_jobs": 1,
            "stale_ai_reservations": 2,
            "review_ai_reservations": 1,
            "statement_differences": 2,
        }
        assert report.status == "attention"
        assert report.will_change_data is False
        # An unrelated pending change must not be flushed by the monitor.
        db.add(
            BillingJob(
                business_key="unflushed",
                kind="fulfill",
                available_at=now - timedelta(hours=1),
            )
        )
        assert inspect_commercial_health(db, now=now).counts == report.counts
        assert len(db.new) == 1
    engine.dispose()


def test_empty_database_is_clear():
    engine = create_engine("sqlite://")
    for model in (BillingOrder, BillingJob, AIUsageReservation, BillingReconciliation):
        model.__table__.create(engine)
    with Session(engine) as db:
        report = inspect_commercial_health(db)
        assert report.status == "clear"
        assert all(count == 0 for count in report.counts.values())
    engine.dispose()
