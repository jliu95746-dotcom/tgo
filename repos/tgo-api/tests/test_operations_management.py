"""Operator authorization keeps tenant scope, ledger history and concurrency guards."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.core.exceptions import TGOAPIException
from app.models import Project, Staff, VisitorSession
from app.models.billing import BillingAudit, SubscriptionPeriod
from app.models.company_account import AICreditBatch, CompanyAccount
from app.schemas.operations_management import AuthorizationChange, MemberControl
from app.services.operations_authorization import (
    change_authorization,
    preview_authorization,
)
from app.services.operations_management import company_detail, directory, update_member
from tests.test_billing_quotes import commercial_company  # noqa: F401
from tests.test_billing_support import prepare
from tests.test_operations_foundation import (
    operations_app,
    operator_password,
)  # noqa: F401


@pytest.fixture
def managed_company(commercial_company):
    db, admin, order, operator, account = prepare(commercial_company)
    for model in (AICreditBatch, VisitorSession):
        model.__table__.create(db.get_bind())
    admin.email_verified_at = datetime.now(timezone.utc)
    order.fulfillment_status = "applied"
    db.commit()
    yield db, admin, commercial_company[2], operator, account, order


def authorization(plan, version=1, **changes):
    return AuthorizationChange(
        request_id=uuid4(),
        expected_version=version,
        plan_id=plan.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=60),
        seats=3,
        ai_credits=10,
        reason="合成测试运营授权",
        **changes,
    )


def test_preview_has_no_mutation_and_commit_is_replay_safe(managed_company):
    db, admin, plan, operator, account, order = managed_company
    payload = authorization(plan)
    before = account.expires_at
    preview = preview_authorization(db, admin.project_id, payload)
    assert preview.after.plan_id == plan.id
    assert preview.after.ai_credits == 10
    assert account.expires_at == before
    assert db.query(BillingAudit).count() == 0
    change_authorization(db, operator, admin.project_id, payload)
    db.commit()
    change_authorization(db, operator, admin.project_id, payload)
    db.commit()
    assert account.version == 2 and account.plan_id == plan.id
    assert account.status == "active"
    assert account.operator_override_until == account.expires_at
    assert db.query(BillingAudit).count() == 1
    assert db.query(AICreditBatch).one().remaining == 10
    assert order.fulfillment_status == "applied" and order.payment_status == "paid"


def test_changed_request_replay_and_stale_version_are_rejected(managed_company):
    db, admin, plan, operator, account, _ = managed_company
    payload = authorization(plan)
    change_authorization(db, operator, admin.project_id, payload)
    db.commit()
    with pytest.raises(TGOAPIException):
        change_authorization(
            db, operator, admin.project_id, payload.model_copy(update={"seats": 4})
        )
    with pytest.raises(TGOAPIException):
        change_authorization(db, operator, admin.project_id, authorization(plan))
    assert account.seat_limit == 3


@pytest.mark.parametrize("bad", ["draft", "seats", "paid_order"])
def test_authorization_validates_plan_seats_and_unresolved_payment(
    managed_company, bad
):
    db, admin, plan, _, _, order = managed_company
    payload = authorization(plan)
    if bad == "draft":
        plan.status = "draft"
    elif bad == "seats":
        db.add(
            Staff(
                project_id=admin.project_id, username="second", password_hash="unused"
            )
        )
        payload = payload.model_copy(update={"seats": 1})
    else:
        order.fulfillment_status = "conflict"
    db.commit()
    with pytest.raises(TGOAPIException):
        preview_authorization(db, admin.project_id, payload)


def test_suspended_company_stays_suspended_after_authorization(managed_company):
    db, admin, plan, operator, account, _ = managed_company
    account.status = "suspended"
    db.commit()
    change_authorization(db, operator, admin.project_id, authorization(plan))
    db.commit()
    assert account.status == "suspended"


def test_legacy_company_can_be_explicitly_authorized(managed_company):
    db, _, plan, operator, _, _ = managed_company
    project = Project(name="未迁入订阅的合成商家", api_key="never-return-this")
    db.add(project)
    db.commit()
    payload = authorization(plan, version=0)
    change_authorization(db, operator, project.id, payload)
    db.commit()
    account = db.get(CompanyAccount, project.id)
    assert account is not None and account.version == 1 and account.status == "active"


def test_details_and_directory_do_not_leak_secrets_or_foreign_members(managed_company):
    db, admin, _, _, _, _ = managed_company
    other = Project(name="另一个商家", api_key="foreign-key")
    db.add(other)
    db.flush()
    foreign = Staff(
        project_id=other.id, username="foreign@example.com", password_hash="secret-hash"
    )
    db.add(foreign)
    db.commit()
    detail = company_detail(db, admin.project_id)
    assert {member.id for member in detail.members} == {admin.id}
    serialized = detail.model_dump_json()
    assert "secret-hash" not in serialized and "foreign-key" not in serialized
    assert "password_hash" not in serialized and "api_key" not in serialized
    result = directory(db, 0, 20, q="另一个", state=None, expiring=False)
    assert result.total == 1 and result.data[0].id == other.id


def test_directory_expired_filter_is_computed_before_pagination(managed_company):
    db, admin, _, _, _, _ = managed_company
    result = directory(db, 0, 1, q="", state="expired", expiring=False)
    assert result.total == 1 and result.data[0].id == admin.project_id
    assert result.data[0].status == "expired"


def test_overview_counts_failed_pending_tasks(managed_company):
    from app.models.billing import BillingJob
    from app.services.operations_management import overview

    db, admin, _, _, _, _ = managed_company
    BillingJob.__table__.create(db.get_bind())
    db.add(
        BillingJob(
            project_id=admin.project_id,
            business_key="synthetic-failure",
            kind="fulfill",
            status="pending",
            available_at=datetime.now(timezone.utc),
            last_error="retry pending",
        )
    )
    db.commit()
    assert overview(db).failed_tasks == 1


def test_last_admin_and_foreign_user_cannot_be_modified(managed_company):
    db, admin, _, operator, _, _ = managed_company
    payload = MemberControl(
        expected_token_version=admin.token_version,
        account_enabled=False,
        reason="合成测试停用账号",
    )
    with pytest.raises(HTTPException, match="管理员"):
        update_member(db, operator, admin.project_id, admin.id, payload)
    with pytest.raises(HTTPException) as error:
        update_member(db, operator, uuid4(), admin.id, payload)
    assert error.value.status_code == 404


def test_role_change_revokes_previous_tokens_and_is_audited(managed_company):
    db, admin, _, operator, _, _ = managed_company
    second = Staff(
        project_id=admin.project_id,
        username="second-admin",
        password_hash="unused",
        role="admin",
    )
    db.add(second)
    db.commit()
    version = admin.token_version
    updated = update_member(
        db,
        operator,
        admin.project_id,
        admin.id,
        MemberControl(expected_token_version=version, role="user", reason="合成测试调整角色"),
    )
    db.commit()
    assert updated.role == "user" and updated.token_version == version + 1
    assert db.query(BillingAudit).one().action == "member.change"
    with pytest.raises(TGOAPIException):
        update_member(
            db,
            operator,
            admin.project_id,
            admin.id,
            MemberControl(
                expected_token_version=version, role="admin", reason="合成测试过期修改"
            ),
        )


def test_disabling_user_with_open_session_is_blocked(managed_company):
    db, admin, _, operator, _, _ = managed_company
    member = Staff(
        project_id=admin.project_id, username="serving-user", password_hash="unused"
    )
    db.add(member)
    db.flush()
    db.add(
        VisitorSession(
            project_id=admin.project_id,
            visitor_id=uuid4(),
            staff_id=member.id,
            status="open",
        )
    )
    db.commit()
    with pytest.raises(TGOAPIException):
        update_member(
            db,
            operator,
            admin.project_id,
            member.id,
            MemberControl(
                expected_token_version=member.token_version,
                account_enabled=False,
                reason="合成测试停止账号",
            ),
        )
    assert member.account_enabled


def test_manual_authorization_is_not_overwritten_by_maintenance(
    managed_company, monkeypatch
):
    from sqlalchemy.orm import Session
    from app.services import billing_maintenance

    db, admin, plan, operator, account, order = managed_company
    now = datetime.now(timezone.utc)
    db.add(
        SubscriptionPeriod(
            project_id=admin.project_id,
            order_id=order.id,
            plan_id=plan.id,
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=29),
            months=1,
            anchor_day=now.day,
            original_price=3000,
            current_price=3000,
            original_definition=plan.definition,
            definition=plan.definition,
        )
    )
    db.commit()
    payload = authorization(plan).model_copy(update={"seats": 5})
    change_authorization(db, operator, admin.project_id, payload)
    db.commit()
    monkeypatch.setattr(
        billing_maintenance, "SessionLocal", lambda: Session(db.get_bind())
    )
    billing_maintenance.maintain_subscriptions()
    db.expire_all()
    assert db.get(CompanyAccount, admin.project_id).seat_limit == 5


@pytest.mark.parametrize("kind", ["seats", "upgrade"])
def test_manual_authorization_cannot_use_inconsistent_paid_period_prices(
    managed_company, kind
):
    from app.schemas.billing import QuoteRequest
    from app.services.billing_quotes import create_quote

    db, admin, plan, operator, _, _ = managed_company
    change_authorization(db, operator, admin.project_id, authorization(plan))
    db.commit()
    with pytest.raises(TGOAPIException, match="运营"):
        create_quote(db, admin, QuoteRequest(kind=kind, plan_id=plan.id))


def test_manually_authorized_company_can_buy_ai_pack(managed_company):
    from app.schemas.billing import QuoteRequest
    from app.services.billing_quotes import create_quote

    db, admin, plan, operator, _, _ = managed_company
    change_authorization(db, operator, admin.project_id, authorization(plan))
    db.commit()
    quote = create_quote(db, admin, QuoteRequest(kind="ai_pack", plan_id=plan.id))
    assert quote.amount == 1000


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path", ["/ops/overview", "/ops/company-directory", "/ops/audit-log"]
)
async def test_merchant_token_cannot_access_operator_management(operations_app, path):
    import httpx
    from app.api.v1.endpoints.operations_management import router
    from app.core.security import create_access_token

    app, _, _, staff, company, _ = operations_app
    app.include_router(router, prefix="/ops")
    token = create_access_token(staff.username, company.id, role="admin")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(path, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_operator_directory_and_details_work_through_http(operations_app):
    import httpx
    from app.api.v1.endpoints.operations_management import router
    from app.models.billing import BillingJob, BillingOrder, BillingPlan, InvoiceRequest
    from app.models.company_invitation import CompanyInvitation
    from tests.test_operations_foundation import login

    app, db, _, staff, company, password = operations_app
    for model in (
        CompanyAccount,
        BillingPlan,
        AICreditBatch,
        CompanyInvitation,
        BillingOrder,
        InvoiceRequest,
        VisitorSession,
        BillingAudit,
        BillingJob,
    ):
        model.__table__.create(db.get_bind())
    app.include_router(router, prefix="/ops")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        logged_in = await login(client, password)
        headers = {"Authorization": f"Bearer {logged_in.json()['access_token']}"}
        result = await client.get("/ops/company-directory?q=企业", headers=headers)
        assert result.status_code == 200, result.text
        assert result.json()["total"] == 1
        detail = await client.get(f"/ops/companies/{company.id}", headers=headers)
        assert detail.status_code == 200, detail.text
        assert detail.json()["members"][0]["id"] == str(staff.id)
        assert (
            company.api_key not in detail.text
            and staff.password_hash not in detail.text
        )
        missing = await client.get(f"/ops/companies/{uuid4()}", headers=headers)
        assert missing.status_code == 404
        overview = await client.get("/ops/overview", headers=headers)
        assert overview.status_code == 200 and overview.json()["total_companies"] == 1
