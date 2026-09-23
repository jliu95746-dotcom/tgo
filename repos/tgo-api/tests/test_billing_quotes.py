"""Commercial quotes use server snapshots, including all prepaid periods."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.models import Project, Staff
from app.models.billing import BillingPlan, BillingQuote, SeatAddon, SubscriptionPeriod
from app.models.company_account import CompanyAccount
from app.models.company_invitation import CompanyInvitation
from app.schemas.billing import PlanDefinition, QuoteRequest
from app.services.billing_quotes import create_quote


@pytest.fixture
def commercial_company():
    engine = create_engine("sqlite://")
    for model in (
        Project,
        Staff,
        BillingPlan,
        CompanyAccount,
        BillingQuote,
        SeatAddon,
        SubscriptionPeriod,
        CompanyInvitation,
    ):
        model.__table__.create(engine)
    with Session(engine) as db:
        project = Project(name="合成计费企业", api_key="synthetic-billing")
        db.add(project)
        db.flush()
        staff = Staff(
            project_id=project.id,
            username="billing-admin",
            password_hash="unused",
            role="admin",
        )
        definition = PlanDefinition(
            name="基础版",
            rank=1,
            monthly_price=3000,
            annual_price=30000,
            seats=3,
            monthly_ai=100,
            knowledge_bytes=100000,
            channel_limit=2,
            seat_monthly_price=500,
            seat_annual_price=5000,
            ai_pack_price=1000,
            ai_pack_replies=100,
        )
        plan = BillingPlan(
            code="basic",
            version=1,
            status="published",
            definition=definition.model_dump(mode="json"),
        )
        db.add_all([staff, plan])
        db.flush()
        now = datetime(2026, 1, 16, tzinfo=timezone.utc)
        account = CompanyAccount(
            project_id=project.id,
            status="trial",
            seat_limit=3,
            expires_at=now + timedelta(days=7),
        )
        db.add(account)
        db.commit()
        yield db, staff, plan, account, now
    engine.dispose()


def test_new_quote_uses_published_server_price(commercial_company):
    db, staff, plan, account, now = commercial_company
    quote = create_quote(
        db, staff, QuoteRequest(kind="subscribe", plan_id=plan.id), now
    )
    assert quote.amount == 3000 and quote.base_version == account.version
    assert quote.details["resulting_seats"] == 3
    assert quote.expires_at == now + timedelta(minutes=15)


def test_draft_and_foreign_staff_cannot_quote(commercial_company):
    db, staff, plan, _, now = commercial_company
    plan.status = "draft"
    db.commit()
    with pytest.raises(TGOAPIException, match="套餐"):
        create_quote(db, staff, QuoteRequest(kind="subscribe", plan_id=plan.id), now)


def test_upgrade_charges_remaining_and_future_periods(commercial_company):
    db, staff, plan, account, now = commercial_company
    account.status = "active"
    account.plan_id = plan.id
    account.billing_months = 1
    account.anchor_day = 1
    account.expires_at = datetime(2026, 3, 1, tzinfo=timezone.utc)
    from uuid import uuid4

    for month in (1, 2):
        db.add(
            SubscriptionPeriod(
                project_id=staff.project_id,
                order_id=uuid4(),
                plan_id=plan.id,
                starts_at=now.replace(month=month, day=1),
                ends_at=now.replace(month=month + 1, day=1),
                months=1,
                anchor_day=1,
                original_price=3000,
                current_price=3000,
                original_definition=plan.definition,
                definition=plan.definition,
            )
        )
    upgraded = BillingPlan(
        code="pro",
        version=1,
        status="published",
        definition={
            **plan.definition,
            "rank": 2,
            "seats": 5,
            "monthly_ai": 200,
            "monthly_price": 6000,
        },
    )
    db.add(upgraded)
    db.commit()
    quote = create_quote(
        db, staff, QuoteRequest(kind="upgrade", plan_id=upgraded.id), now
    )
    assert quote.amount == 4549  # ceil(3000 * 16/31 + 3000)
    assert quote.details["additional_ai"] == 51
    assert quote.details["ends_at"].startswith("2026-03-01")
    assert len(quote.details["periods"]) == 2


def test_expired_account_cannot_purchase_ai_pack(commercial_company):
    db, staff, plan, account, now = commercial_company
    account.status = "expired"
    account.plan_id = plan.id
    db.commit()
    with pytest.raises(TGOAPIException, match="有效"):
        create_quote(db, staff, QuoteRequest(kind="ai_pack"), now)
