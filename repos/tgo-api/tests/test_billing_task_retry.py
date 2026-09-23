"""Operators can wake failed work, but cannot bypass a lease or review decision."""

from datetime import datetime, timedelta, timezone

import pytest

from app.core.exceptions import TGOAPIException
from app.models.billing import BillingAudit, BillingJob
from app.schemas.operations_tasks import RetryTask
from app.services.operations_tasks import retry_task
from tests.test_billing_support import prepare
from tests.test_billing_quotes import commercial_company  # noqa: F401


@pytest.mark.parametrize(
    "status,locked,allowed",
    [
        ("pending", False, True),
        ("pending", True, False),
        ("done", False, False),
        ("review", False, False),
    ],
)
def test_task_retry_preserves_business_decisions(
    commercial_company, status, locked, allowed
):
    db, staff, _, operator, _ = prepare(commercial_company)
    now = datetime.now(timezone.utc)
    BillingJob.__table__.create(db.get_bind(), checkfirst=True)
    job = BillingJob(
        project_id=staff.project_id,
        business_key="synthetic-retry",
        kind="fulfill",
        status=status,
        available_at=now + timedelta(hours=1),
        locked_until=now + timedelta(minutes=5) if locked else None,
        last_error="TimeoutError",
    )
    db.add(job)
    db.commit()
    payload = RetryTask(reason="合成测试恢复异常任务")
    if allowed:
        retry_task(db, operator, job.id, payload)
        db.commit()
        assert job.status == "pending" and job.attempts == 0
        assert db.query(BillingAudit).one().action == "task_retry"
    else:
        with pytest.raises(TGOAPIException):
            retry_task(db, operator, job.id, payload)
        assert db.query(BillingAudit).count() == 0
