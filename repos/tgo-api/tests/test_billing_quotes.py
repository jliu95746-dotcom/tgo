"""Commercial quotes use server snapshots, including all prepaid periods."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.models import Project, Staff
from app.models.billing import BillingAudit, BillingOrder, BillingPlan, BillingQuote, SeatAddon, SubscriptionPeriod
from app.models.company_account import AICreditBatch, CompanyAccount
from app.models.company_invitation import CompanyInvitation
from app.schemas.billing import STANDARD_PLAN_RANKS, PlanCreate, PlanDefinition, QuoteRequest
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


def test_custom_plan_is_visible_and_quotable_only_by_its_enterprise(commercial_company):
    from app.services.billing_plans import catalogue_plans

    db, staff, standard, _, now = commercial_company
    custom = BillingPlan(
        code=f"custom-{staff.project_id.hex}", version=1, status="published",
        definition={**standard.definition, "target_project_id": str(staff.project_id)},
    )
    legacy = BillingPlan(
        code="legacy-extra", version=1, status="published", definition=standard.definition,
    )
    db.add_all([custom, legacy])
    db.flush()
    assert [plan.id for plan in catalogue_plans(db)] == [standard.id]
    assert {plan.id for plan in catalogue_plans(db, staff.project_id)} == {standard.id, custom.id}
    assert [plan.id for plan in catalogue_plans(db, uuid4())] == [standard.id]
    assert create_quote(
        db, staff, QuoteRequest(kind="subscribe", plan_id=custom.id, months=12), now
    ).amount == 30000
    with pytest.raises(TGOAPIException, match="套餐"):
        create_quote(db, staff, QuoteRequest(kind="subscribe", plan_id=legacy.id), now)
    custom.definition = {**custom.definition, "target_project_id": str(uuid4())}
    db.flush()
    with pytest.raises(TGOAPIException, match="套餐"):
        create_quote(db, staff, QuoteRequest(kind="subscribe", plan_id=custom.id), now)


@pytest.mark.parametrize(("monthly", "annual"), [(18800, 179800), (38800, 371800), (58800, 563800)])
def test_standard_annual_price_rounds_down_to_ending_eight(monthly, annual):
    from app.services.billing_plans import standard_annual_price

    assert standard_annual_price(monthly) == annual


def test_standard_drafts_publish_together_with_shared_add_on_terms(commercial_company):
    from app.services.billing_plans import (
        catalogue_plans, create_standard_drafts, publish_standard_drafts, standard_annual_price,
    )

    db, _, original, _, _ = commercial_company
    BillingAudit.__table__.create(db.get_bind())
    operator = SimpleNamespace(id=uuid4())
    drafts = [
        PlanCreate(code=code, definition=PlanDefinition.model_validate({
            **original.definition, "rank": rank, "monthly_price": price,
            "annual_price": standard_annual_price(price), "channel_limit": None,
        }))
        for code, rank, price in [("basic", 1, 18800), ("standard", 2, 38800), ("pro", 3, 58800)]
    ]
    inconsistent = drafts[-1].model_copy(update={
        "definition": drafts[-1].definition.model_copy(update={"ai_pack_price": 2000}),
    })
    with pytest.raises(TGOAPIException, match="加购"):
        create_standard_drafts(db, operator, [*drafts[:2], inconsistent])
    created = create_standard_drafts(db, operator, drafts)
    assert [plan.id for plan in catalogue_plans(db)] == [original.id]
    publish_standard_drafts(db, operator, [plan.id for plan in created])
    assert [plan.definition["name"] for plan in catalogue_plans(db)] == ["轻量额度", "常用额度", "扩展额度"]
    assert original.status == "retired" and original.definition["monthly_price"] == 3000


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


@pytest.mark.parametrize(
    "code,next_code,next_name,seats,next_seats,allowance,next_allowance,packs",
    [
        ("basic", "standard", "常用额度", 2, 4, 3500, 10000, 6),
        ("standard", "pro", "扩展额度", 4, 11, 10000, 20000, 9),
    ],
)
def test_standard_add_ons_respect_next_tier_across_purchases(
    commercial_company, code, next_code, next_name, seats, next_seats,
    allowance, next_allowance, packs,
):
    db, staff, plan, account, now = commercial_company
    BillingOrder.__table__.create(db.get_bind())
    AICreditBatch.__table__.create(db.get_bind())
    plan.code = code
    plan.definition = {
        **plan.definition, "rank": STANDARD_PLAN_RANKS[code],
        "seats": seats, "monthly_ai": allowance,
        "ai_pack_price": 3000, "ai_pack_replies": 1000,
    }
    higher = BillingPlan(
        code=next_code, version=1, status="published",
        definition={
            **plan.definition, "name": next_name, "rank": STANDARD_PLAN_RANKS[next_code],
            "seats": next_seats, "monthly_ai": next_allowance,
        },
    )
    account.status = "active"
    account.plan_id = plan.id
    account.seat_limit = seats
    account.billing_months = 12
    account.anchor_day = 31
    start = datetime(2025, 12, 31, tzinfo=timezone.utc)
    account.expires_at = datetime(2026, 12, 31, tzinfo=timezone.utc)
    db.add_all([higher, SubscriptionPeriod(
        project_id=staff.project_id, order_id=uuid4(), plan_id=plan.id,
        starts_at=start, ends_at=account.expires_at, months=12, anchor_day=31,
        original_price=30000, current_price=30000,
        original_definition=plan.definition, definition=plan.definition,
    )])
    db.flush()

    seat_quote = QuoteRequest(kind="seats", months=12)
    assert create_quote(db, staff, seat_quote, now).details["resulting_seats"] == seats + 1
    account.seat_limit = next_seats - 1
    with pytest.raises(TGOAPIException, match=next_name):
        create_quote(db, staff, seat_quote, now)

    pack = QuoteRequest(kind="ai_pack", months=12, quantity=packs)
    next_month = datetime(2026, 1, 31, tzinfo=timezone.utc)
    near_month_end = next_month - timedelta(minutes=1)
    quote = create_quote(db, staff, pack, near_month_end)
    assert quote.amount == packs * 3000
    assert quote.details["additional_ai"] == packs * 1000
    assert quote.details["ends_at"] == next_month.isoformat().replace("+00:00", "Z")
    assert quote.expires_at == next_month
    db.add(AICreditBatch(
        project_id=staff.project_id, source_key="used-pack", kind="pack",
        amount=packs * 1000, remaining=0, created_at=now - timedelta(days=1),
        expires_at=next_month,
    ))
    db.flush()
    with pytest.raises(TGOAPIException, match=next_name):
        create_quote(db, staff, QuoteRequest(kind="ai_pack", months=12), now)
    assert create_quote(db, staff, pack, next_month).details["additional_ai"] == packs * 1000
    with pytest.raises(TGOAPIException, match=next_name):
        create_quote(db, staff, pack.model_copy(update={"quantity": packs + 1}), next_month)
