"""Verification cannot grant a repeated trial or revive a disabled account."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Project, Staff
from app.models.company_account import (
    CompanyAccount,
    EmailAction,
    EmailOutbox,
    AICreditBatch,
)
from app.services.company_email import (
    complete_password_reset,
    complete_verification,
    complete_verification_code,
    verification_code_hash,
)
from app.models.platform_operator import PlatformOperator
from app.models.trial_activation_code import TrialActivationCode
from app.models.trial_policy import TrialPolicy
from app.services.trial_activation import issue_code, redeem_code


@pytest.fixture
def pending_company():
    engine = create_engine("sqlite://")
    for model in (
        Project,
        Staff,
        CompanyAccount,
        EmailAction,
        EmailOutbox,
        AICreditBatch,
        TrialPolicy,
        PlatformOperator,
        TrialActivationCode,
    ):
        model.__table__.create(engine)
    with Session(engine) as db:
        company = Project(name="合成待验证企业", api_key="fixture-only")
        db.add(company)
        db.flush()
        staff = Staff(
            project_id=company.id,
            username="trial@example.com",
            password_hash="unused",
            role="admin",
            account_enabled=False,
        )
        account = CompanyAccount(project_id=company.id)
        operator = PlatformOperator(
            email="operator@example.com", name="Operator", password_hash="unused"
        )
        db.add_all([staff, account, operator])
        db.flush()
        action = EmailAction(
            project_id=company.id,
            staff_id=staff.id,
            purpose="verify",
            token_hash=sha256(b"synthetic-link").hexdigest(),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db.add(action)
        db.commit()
        yield db, staff, account, action
    engine.dispose()


def test_verify_enables_login_without_granting_trial(pending_company):
    db, staff, account, _ = pending_company
    complete_verification(db, "synthetic-link")
    db.commit()
    complete_verification(db, "synthetic-link")
    db.commit()
    assert staff.account_enabled
    assert staff.email_verified_at is not None
    assert not account.trial_granted and account.status == "pending"
    assert account.expires_at is None
    assert db.query(AICreditBatch).count() == 0


def test_code_expires_and_cannot_verify_another_email(pending_company):
    db, staff, account, action = pending_company
    action.token_hash = verification_code_hash(staff.id, "123456")
    db.commit()
    for email, code in (
        ("another@example.com", "123456"),
        (staff.username, "654321"),
    ):
        with pytest.raises(HTTPException) as error:
            complete_verification_code(db, email, code)
        assert error.value.status_code == 400
    action.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    with pytest.raises(HTTPException) as error:
        complete_verification_code(db, staff.username, "123456")
    assert error.value.status_code == 400
    assert not staff.account_enabled and not account.trial_granted


def test_trial_uses_current_policy_once_and_keeps_existing_grant(pending_company):
    from uuid import uuid4

    db, staff, _, _ = pending_company
    operator = db.query(PlatformOperator).one()
    db.add(TrialPolicy(version=1, ai_replies=250, operator_id=uuid4(), reason="合成额度配置"))
    db.commit()
    complete_verification(db, "synthetic-link")
    code, _ = issue_code(db, operator)
    redeem_code(db, staff, code)
    db.commit()
    db.add(
        TrialPolicy(version=2, ai_replies=300, operator_id=uuid4(), reason="合成新额度配置")
    )
    db.commit()
    with pytest.raises(HTTPException):
        redeem_code(db, staff, code)
    assert db.query(AICreditBatch).one().amount == 250


def test_replayed_link_cannot_reenable_disabled_account(pending_company):
    db, staff, _, _ = pending_company
    complete_verification(db, "synthetic-link")
    staff.account_enabled = False
    db.commit()
    complete_verification(db, "synthetic-link")
    assert not staff.account_enabled


@pytest.mark.parametrize("reason", ["expired", "wrong_purpose", "wrong_token"])
def test_invalid_verification_never_activates(pending_company, reason):
    db, staff, account, action = pending_company
    token = "synthetic-link"
    if reason == "expired":
        action.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    elif reason == "wrong_purpose":
        action.purpose = "reset"
    else:
        token = "different-link"
    db.commit()
    with pytest.raises(HTTPException):
        complete_verification(db, token)
    assert not staff.account_enabled and not account.trial_granted
    assert db.query(AICreditBatch).count() == 0


def test_reset_invalidates_other_links_and_keeps_disabled_state(
    pending_company, monkeypatch
):
    from app.services import company_email

    db, staff, _, action = pending_company
    action.purpose = "reset"
    staff.email_verified_at = datetime.now(timezone.utc)
    second = EmailAction(
        project_id=staff.project_id,
        staff_id=staff.id,
        purpose="reset",
        token_hash=sha256(b"another-link").hexdigest(),
        expires_at=action.expires_at,
    )
    db.add(second)
    db.commit()
    monkeypatch.setattr(company_email, "get_password_hash", lambda _: "updated-hash")
    complete_password_reset(db, "synthetic-link", "synthetic-password")
    db.commit()
    assert staff.password_hash == "updated-hash"
    assert staff.token_version == 2
    assert not staff.account_enabled
    assert second.used_at is not None
    with pytest.raises(HTTPException):
        complete_password_reset(db, "another-link", "other-password")


def test_expired_mail_is_not_delivered(pending_company):
    from app.services.company_mail_delivery import claim_mail

    db, staff, _, action = pending_company
    action.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    mail = EmailOutbox(
        project_id=staff.project_id,
        action_id=action.id,
        encrypted_payload="must-be-cleared",
        available_at=action.expires_at,
    )
    db.add(mail)
    db.commit()
    assert claim_mail(db) is None
    assert mail.status == "expired" and mail.encrypted_payload == ""
