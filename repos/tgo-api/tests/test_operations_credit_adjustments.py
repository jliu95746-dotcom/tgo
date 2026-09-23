"""Manual compensation is reasoned, replay-safe and cannot erase reserved credits."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest

from app.core.exceptions import TGOAPIException
from app.models.billing import BillingAudit
from app.models.company_account import AICreditBatch
from app.schemas.operations_companies import CreditAdjustment
from app.services.operations_companies import adjust_credit
from tests.test_billing_support import prepare
from tests.test_billing_quotes import commercial_company  # noqa: F401


def test_adjustments_replay_once_and_keep_original_grants(commercial_company):
    db, staff, _, operator, _ = prepare(commercial_company)
    AICreditBatch.__table__.create(db.get_bind())
    addition = CreditAdjustment(
        request_id=uuid4(),
        delta=5,
        reason="合成测试补偿次数",
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    adjust_credit(db, operator, staff.project_id, addition)
    db.commit()
    adjust_credit(db, operator, staff.project_id, addition)
    db.commit()
    batch = db.query(AICreditBatch).one()
    assert batch.remaining == 5 and db.query(BillingAudit).count() == 1
    removal = CreditAdjustment(request_id=uuid4(), delta=-2, reason="合成测试纠正次数")
    adjust_credit(db, operator, staff.project_id, removal)
    db.commit()
    assert batch.remaining == 3 and batch.amount == 5
    with pytest.raises(TGOAPIException):
        adjust_credit(
            db, operator, staff.project_id, removal.model_copy(update={"delta": -1})
        )
    with pytest.raises(TGOAPIException):
        adjust_credit(
            db,
            operator,
            staff.project_id,
            CreditAdjustment(request_id=uuid4(), delta=-4, reason="合成测试超额扣除"),
        )
    assert batch.remaining == 3
