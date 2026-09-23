"""Operator changes affect future trials and reject stale concurrent edits."""

import pytest

from app.core.exceptions import TGOAPIException
from app.models.trial_policy import TrialPolicy
from app.schemas.trial_policy import TrialPolicyChange
from app.services.trial_policy import read_policy, change_policy
from tests.test_billing_support import prepare
from tests.test_billing_quotes import commercial_company  # noqa: F401


def test_policy_defaults_and_version_conflict(commercial_company):
    db, _, _, operator, _ = prepare(commercial_company)
    TrialPolicy.__table__.create(db.get_bind())
    initial = read_policy(db)
    assert initial.version == 0 and initial.ai_replies == 100
    updated = change_policy(
        db,
        operator,
        TrialPolicyChange(expected_version=0, ai_replies=200, reason="合成测试调整试用额度"),
    )
    db.commit()
    assert updated.version == 1 and read_policy(db).ai_replies == 200
    with pytest.raises(TGOAPIException) as error:
        change_policy(
            db,
            operator,
            TrialPolicyChange(expected_version=0, ai_replies=300, reason="合成测试旧页面提交"),
        )
    assert error.value.status_code == 409
    assert db.query(TrialPolicy).count() == 1
