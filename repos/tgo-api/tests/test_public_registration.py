"""Public signup creates a new tenant and never accepts tenant/role overrides."""

from unittest.mock import AsyncMock
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints import registration
from app.core.database import get_db
from app.core import security
from app.models import Platform, PlatformTypeDefinition, Project, Staff, SystemSetup
from app.services import project_registration


@compiles(JSONB, "sqlite")
def compile_jsonb_for_test(_element, _compiler, **_kwargs):
    return "JSON"


@pytest.fixture
def signup_app(monkeypatch):
    monkeypatch.setattr(project_registration.settings, "SAAS_ENABLED", False)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    for model in (Project, Staff, PlatformTypeDefinition, Platform, SystemSetup):
        model.__table__.create(engine)
    with Session(engine) as db:
        db.add(SystemSetup(is_installed=True, admin_created=True, skip_llm_config=True))
        db.commit()
        app = FastAPI()
        app.include_router(registration.router, prefix="/staff")
        app.dependency_overrides[get_db] = lambda: db
        monkeypatch.setattr(registration, "limit_registration", AsyncMock())
        monkeypatch.setattr(
            project_registration, "get_password_hash", lambda value: "hash:" + value
        )
        monkeypatch.setattr(
            project_registration.settings, "PUBLIC_REGISTRATION_ENABLED", True
        )
        monkeypatch.setattr(
            project_registration.wukongim_client, "create_channel", AsyncMock()
        )
        yield app, db
    engine.dispose()


def data(**changes):
    return {
        "username": "owner@example.com",
        "password": "fixture-password-123",
        **changes,
    }


@pytest.mark.asyncio
async def test_saas_signup_waits_for_email_and_encrypts_outbox(signup_app, monkeypatch):
    import re
    from app.api.v1.endpoints import company_email
    from app.models.trial_policy import TrialPolicy
    from app.services.company_email import MailPayload
    from app.models.company_account import CompanyAccount, EmailAction, EmailOutbox, AICreditBatch
    from app.utils.crypto import decrypt_str, encrypt_str
    from app.schemas.platform_models import PlatformModelDefinition, StoredPlatformModel
    app, db = signup_app
    for model in (CompanyAccount, EmailAction, EmailOutbox, AICreditBatch):
        model.__table__.create(db.get_bind())
    TrialPolicy.__table__.create(db.get_bind())
    app.include_router(company_email.router, prefix="/staff")
    monkeypatch.setattr(company_email, "limit_registration", AsyncMock())
    config = project_registration.settings
    monkeypatch.setattr(config, "SAAS_ENABLED", True)
    monkeypatch.setattr(config, "SAAS_REGISTRATION_ENABLED", True)
    monkeypatch.setattr(config, "SAAS_BILLING_ENABLED", True)
    monkeypatch.setattr(config, "SAAS_WEB_BASE_URL", "https://example.com")
    monkeypatch.setattr(config, "SAAS_SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(config, "SAAS_SMTP_FROM", "mail@example.com")
    db.query(SystemSetup).one().config = {"saas_platform_model": StoredPlatformModel(
        version=1, definition=PlatformModelDefinition(model="synthetic", provider_kind="openai"),
        encrypted_api_key=encrypt_str("synthetic-platform-key")).model_dump(mode="json")}
    db.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/staff/register", json=data())
    assert response.status_code == 201
    assert response.json()["account_enabled"] is False
    assert db.query(CompanyAccount).one().status == "pending"
    assert db.query(AICreditBatch).count() == 0
    mail = db.query(EmailOutbox).one()
    assert "owner@example.com" not in mail.encrypted_payload
    assert "#token=" not in decrypt_str(mail.encrypted_payload)
    assert "token" not in response.text
    staff = db.query(Staff).one()
    assert security.resolve_staff_token(db, security.create_access_token(staff.username, staff.project_id)) is None

    # Exercise the code delivered by registration; only a successful code
    # submission may grant a trial.
    payload = MailPayload.model_validate_json(decrypt_str(mail.encrypted_payload))
    code = re.search(r"(?<!\d)\d{6}(?!\d)", payload.body).group()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        wrong = await client.post(
            "/staff/verify-email-code",
            json={
                "email": "owner@example.com",
                "code": "000000" if code != "000000" else "999999",
            },
        )
        assert wrong.status_code == 400
        verified = await client.post(
            "/staff/verify-email-code",
            json={"email": "OWNER@example.com", "code": code},
        )
        assert verified.status_code == 200
        account = db.query(CompanyAccount).one()
        assert account.status == "pending" and not account.trial_granted
        assert account.expires_at is None
        assert staff.account_enabled and staff.email_verified_at is not None
        assert security.resolve_staff_token(
            db, security.create_access_token(staff.username, staff.project_id)
        ) is not None
        replay = await client.post(
            "/staff/verify-email-code",
            json={"email": "owner@example.com", "code": code},
        )
        assert replay.status_code == 400 and code not in replay.text
        assert account.expires_at is None
        assert db.query(AICreditBatch).count() == 0


@pytest.mark.asyncio
async def test_saas_signup_waits_for_usable_platform_model(signup_app, monkeypatch):
    app, db = signup_app
    config = project_registration.settings
    for name in ("SAAS_ENABLED", "SAAS_REGISTRATION_ENABLED", "SAAS_BILLING_ENABLED"):
        monkeypatch.setattr(config, name, True)
    monkeypatch.setattr(config, "SAAS_WEB_BASE_URL", "https://example.com")
    monkeypatch.setattr(config, "SAAS_SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(config, "SAAS_SMTP_FROM", "mail@example.com")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        result = await client.post("/staff/register", json=data())
    assert result.status_code == 503
    assert db.query(Project).count() == 0


@pytest.mark.asyncio
async def test_public_signup_creates_two_distinct_projects(signup_app):
    app, db = signup_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.post("/staff/register", json=data(project_name="独立项目一"))
        second = await client.post(
            "/staff/register", json=data(username="other@example.com")
        )
    assert first.status_code == second.status_code == 201
    assert first.json()["project_id"] != second.json()["project_id"]
    assert first.json()["role"] == second.json()["role"] == "admin"
    assert "password" not in first.text and "api_key" not in first.text
    assert db.query(Project).count() == db.query(Staff).count() == 2
    assert db.query(Platform).count() == 2
    assert all(
        row.type == "website" and row.ai_mode == "off" for row in db.query(Platform)
    )
    assert db.query(Project).first().name == "独立项目一"
    assert db.query(Staff).first().password_hash == "hash:fixture-password-123"
    project_registration.wukongim_client.create_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_email_does_not_create_orphan_project(signup_app):
    app, db = signup_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.post("/staff/register", json=data())
        second = await client.post(
            "/staff/register", json=data(username="OWNER@example.com")
        )
    assert first.status_code == 201 and second.status_code == 409
    assert db.query(Project).count() == 1 and db.query(Staff).count() == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [
        {"project_id": "00000000-0000-0000-0000-000000000001"},
        {"role": "admin"},
        {"api_key": "chosen-key"},
    ],
)
async def test_signup_rejects_privilege_and_tenant_injection(signup_app, extra):
    app, db = signup_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post("/staff/register", json=data(**extra))
    assert result.status_code == 422
    assert db.query(Project).count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"username": "not-an-email"},
        {"password": "short"},
        {"password": "中" * 25},
        {"project_name": "   "},
    ],
)
async def test_invalid_signup_is_rejected_before_writing(signup_app, changes):
    app, db = signup_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post("/staff/register", json=data(**changes))
    assert result.status_code == 422 and db.query(Project).count() == 0


@pytest.mark.asyncio
async def test_disabled_registration_and_uninstalled_system_remain_closed(
    signup_app, monkeypatch
):
    app, db = signup_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        monkeypatch.setattr(
            project_registration.settings, "PUBLIC_REGISTRATION_ENABLED", False
        )
        result = await client.post("/staff/register", json=data())
        assert result.status_code == 403
        monkeypatch.setattr(
            project_registration.settings, "PUBLIC_REGISTRATION_ENABLED", True
        )
        db.query(SystemSetup).first().is_installed = False
        db.commit()
        result = await client.post("/staff/register", json=data())
        assert result.status_code == 409
    assert db.query(Project).count() == 0


@pytest.mark.asyncio
async def test_database_failure_rolls_back_account_and_project(signup_app, monkeypatch):
    app, db = signup_app

    def fail_commit():
        raise SQLAlchemyError("Database temporarily unavailable")

    monkeypatch.setattr(db, "commit", fail_commit)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post("/staff/register", json=data())
    assert result.status_code == 503
    assert (
        db.query(Project).count()
        == db.query(Staff).count()
        == db.query(Platform).count()
        == 0
    )


@pytest.mark.asyncio
async def test_registered_email_can_log_in_with_different_case(signup_app, monkeypatch):
    app, db = signup_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post("/staff/register", json=data())
    assert result.status_code == 201
    monkeypatch.setattr(
        security, "verify_password", lambda plain, hashed: hashed == "hash:" + plain
    )
    user = security.authenticate_user(db, " OWNER@example.com ", "fixture-password-123")
    assert user and user.username == "owner@example.com"
    assert security.authenticate_user(db, "OWNER@example.com", "wrong") is None


@pytest.mark.asyncio
async def test_login_channel_membership_is_limited_to_current_project(signup_app):
    app, db = signup_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post("/staff/register", json=data())
        await client.post("/staff/register", json=data(username="other@example.com"))
    owner = db.query(Staff).filter_by(username="owner@example.com").one()
    colleague = Staff(
        project_id=owner.project_id,
        username="colleague@example.com",
        password_hash="hash:unused",
        role="user",
        status="offline",
    )
    deleted = Staff(
        project_id=owner.project_id,
        username="deleted@example.com",
        password_hash="hash:unused",
        role="user",
        status="offline",
        deleted_at=datetime.now(timezone.utc),
    )
    db.add_all([colleague, deleted])
    db.commit()
    await project_registration.ensure_project_staff_channel(db, owner)
    call = project_registration.wukongim_client.create_channel.await_args.kwargs
    assert call["channel_id"] == project_registration.build_project_staff_channel_id(
        owner.project_id
    )
    assert set(call["subscribers"]) == {f"{owner.id}-staff", f"{colleague.id}-staff"}
