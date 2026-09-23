"""Operator-issued codes grant one trial to one verified company."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.models import Project, Staff
from app.models.company_account import AICreditBatch, CompanyAccount
from app.models.platform_operator import PlatformOperator
from app.models.trial_activation_code import TrialActivationCode
from app.models.trial_policy import TrialPolicy
from app.api.v1.endpoints import trial_activation
from app.api.v1.endpoints.operations import require_operator
from app.core import security
from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import TGOAPIException, tgo_api_exception_handler
from app.services.trial_activation import issue_code, redeem_code
from app.services.company_email import utc


@pytest.fixture
def activation_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    for model in (
        Project, Staff, PlatformOperator, CompanyAccount,
        AICreditBatch, TrialPolicy, TrialActivationCode,
    ):
        model.__table__.create(engine)
    with Session(engine) as db:
        operator = PlatformOperator(
            email="operator@example.com", name="Operator", password_hash="unused"
        )
        project = Project(name="Pending company", api_key="pending-key")
        db.add_all([operator, project])
        db.flush()
        staff = Staff(
            project_id=project.id,
            username="owner@example.com",
            password_hash="unused",
            role="admin",
            account_enabled=True,
            email_verified_at=datetime.now(timezone.utc),
        )
        account = CompanyAccount(project_id=project.id)
        db.add_all([staff, account])
        db.commit()
        yield db, operator, staff, account
    engine.dispose()


def test_operator_code_grants_one_trial_only_after_redemption(activation_db):
    db, operator, staff, account = activation_db
    code, record = issue_code(db, operator)
    db.commit()
    assert account.status == "pending" and not account.trial_granted
    assert code not in record.code_hash
    assert db.query(AICreditBatch).count() == 0

    redeem_code(db, staff, code)
    db.commit()
    assert account.status == "trial" and account.trial_granted
    assert account.seat_limit == 3
    assert utc(account.expires_at) > datetime.now(timezone.utc)
    assert record.redeemed_project_id == staff.project_id
    assert db.query(AICreditBatch).one().remaining == 100

    with pytest.raises(HTTPException) as error:
        redeem_code(db, staff, code)
    assert error.value.status_code == 409
    assert db.query(AICreditBatch).count() == 1

    other_project = Project(name="Other company", api_key="other-key")
    db.add(other_project)
    db.flush()
    other_staff = Staff(
        project_id=other_project.id, username="other@example.com",
        password_hash="unused", role="admin", account_enabled=True,
        email_verified_at=datetime.now(timezone.utc),
    )
    other_account = CompanyAccount(project_id=other_project.id)
    db.add_all([other_staff, other_account])
    db.commit()
    with pytest.raises(HTTPException) as error:
        redeem_code(db, other_staff, code)
    assert error.value.status_code == 400
    assert other_account.status == "pending" and not other_account.trial_granted
    assert db.query(AICreditBatch).count() == 1


def test_invalid_expired_and_unverified_codes_do_not_grant(activation_db):
    db, operator, staff, account = activation_db
    code, record = issue_code(db, operator)
    db.commit()
    with pytest.raises(HTTPException):
        redeem_code(db, staff, "YJ-incorrect-code")
    staff.email_verified_at = None
    db.commit()
    with pytest.raises(HTTPException) as error:
        redeem_code(db, staff, code)
    assert error.value.status_code == 403
    staff.email_verified_at = datetime.now(timezone.utc)
    record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    with pytest.raises(HTTPException) as error:
        redeem_code(db, staff, code)
    assert error.value.status_code == 400
    assert account.status == "pending" and not account.trial_granted


@pytest.mark.asyncio
async def test_code_endpoints_keep_operator_and_company_authority_separate(
    activation_db, monkeypatch
):
    db, operator, staff, account = activation_db
    monkeypatch.setattr(settings, "SAAS_ENABLED", True)
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", True)
    monkeypatch.setattr(settings, "OPS_ENABLED", True)
    monkeypatch.setattr(trial_activation, "limit_registration", AsyncMock())
    app = FastAPI()
    app.add_exception_handler(TGOAPIException, tgo_api_exception_handler)
    app.include_router(trial_activation.ops_router, prefix="/ops")
    app.include_router(trial_activation.company_router, prefix="/company")
    app.dependency_overrides[get_db] = lambda: db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        unauthorized = await client.post("/ops/trial-codes")
        assert unauthorized.status_code == 401
        app.dependency_overrides[require_operator] = lambda: operator
        created = await client.post("/ops/trial-codes")
        assert created.status_code == 201
        assert created.headers["cache-control"] == "no-store"
        code = created.json()["code"]
        listed = await client.get("/ops/trial-codes")
        assert listed.status_code == 200
        assert "code" not in listed.json()[0]
        assert "code_hash" not in listed.text
        assert code not in listed.text

        invalid = await client.post("/company/trial-activation", json={"code": code})
        assert invalid.status_code in {401, 403}
        member = Staff(
            project_id=staff.project_id,
            username="member@example.com",
            password_hash="unused", role="user", account_enabled=True,
        )
        db.add(member)
        db.commit()
        member_token = security.create_access_token(member.username, member.project_id)
        denied = await client.post(
            "/company/trial-activation", json={"code": code},
            headers={"Authorization": f"Bearer {member_token}"},
        )
        assert denied.status_code == 403
        assert not account.trial_granted

        admin_token = security.create_access_token(staff.username, staff.project_id)
        redeemed = await client.post(
            "/company/trial-activation", json={"code": code},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert redeemed.status_code == 200
        assert account.trial_granted and account.status == "trial"
