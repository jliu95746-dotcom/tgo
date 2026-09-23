"""Realtime actors must be authenticated and scoped to current company data."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from jose import jwt
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.core.security import create_access_token, resolve_staff_token
from app.models import Platform, Project, Staff, Visitor
from app.services.im_access import (
    authenticate_im_actor,
    authorize_im_channel,
    issue_visitor_im_token,
)


@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(_type, compiler, **kwargs):
    return "JSON"


@pytest.fixture
def records():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for model in (Project, Staff, Platform, Visitor):
        model.__table__.create(engine)
    with Session(engine) as db:
        companies = [Project(name=name, api_key=name) for name in ("A", "B")]
        db.add_all(companies)
        db.flush()
        staff = [
            Staff(
                project_id=company.id,
                username=company.name,
                role="admin",
                password_hash="unused",
            )
            for company in companies
        ]
        platforms = [
            Platform(
                project_id=company.id,
                name=company.name,
                type="website",
                api_key=company.name,
                is_active=True,
            )
            for company in companies
        ]
        db.add_all(staff + platforms)
        db.flush()
        visitors = [
            Visitor(
                project_id=company.id,
                platform_id=platform.id,
                platform_open_id=company.name,
            )
            for company, platform in zip(companies, platforms)
        ]
        db.add_all(visitors)
        db.commit()
        yield db, companies, staff, platforms, visitors
    engine.dispose()


def staff_token(staff):
    return create_access_token(staff.username, staff.project_id, staff.role)


def visitor_token(visitor):
    return issue_visitor_im_token(visitor, timedelta(hours=1))


def test_connect_token_cannot_impersonate_another_staff_uid(records):
    db, _, staff, _, _ = records
    actor = authenticate_im_actor(
        db, f"{staff[0].id}-staff", staff_token(staff[0])
    )
    assert actor.id == staff[0].id
    with pytest.raises(TGOAPIException) as denied:
        authenticate_im_actor(
            db, f"{staff[1].id}-staff", staff_token(staff[0])
        )
    assert denied.value.status_code == 401


@pytest.mark.parametrize("role", ["admin", "user"])
@pytest.mark.asyncio
async def test_staff_socket_cannot_send_or_subscribe_to_foreign_channels(
    records, role
):
    db, companies, staff, _, visitors = records
    staff[0].role = role
    for channel_id, channel_type in [
        (f"{staff[1].id}-staff", 1),
        (f"{visitors[1].id}-vtr", 251),
        (f"{companies[1].id}-prj", 249),
        ("public-channel", 3),
    ]:
        with pytest.raises(TGOAPIException):
            await authorize_im_channel(db, staff[0], channel_id, channel_type)
    await authorize_im_channel(db, staff[0], f"{visitors[0].id}-vtr", 251)


@pytest.mark.asyncio
async def test_visitor_only_has_own_reception_channel(records):
    db, _, staff, _, visitors = records
    actor = authenticate_im_actor(
        db, f"{visitors[0].id}-vtr", visitor_token(visitors[0])
    )
    assert actor.id == visitors[0].id
    await authorize_im_channel(db, actor, f"{actor.id}-vtr", 251)
    for target, channel_type in [
        (f"{visitors[1].id}-vtr", 251),
        (f"{staff[0].id}-staff", 1),
        (f"{actor.id}-vtr", 3),
        (str(uuid4()), 2),
    ]:
        with pytest.raises(TGOAPIException) as denied:
            await authorize_im_channel(db, actor, target, channel_type)
        assert denied.value.status_code == 403


@pytest.mark.parametrize(
    "change",
    [
        "visitor_deleted",
        "visitor_moved",
        "platform_disabled",
        "platform_deleted",
        "platform_moved",
        "visitor_reassigned_platform",
        "company_deleted",
        "token_expired",
        "wrong_uid",
    ],
)
def test_visitor_access_revokes_when_identity_or_channel_changes(
    records, change
):
    db, companies, _, platforms, visitors = records
    visitor = visitors[0]
    token = visitor_token(visitor)
    uid = f"{visitor.id}-vtr"
    now = datetime.now(timezone.utc)
    if change == "visitor_deleted":
        visitor.deleted_at = now
    elif change == "visitor_moved":
        visitor.project_id = companies[1].id
    elif change == "platform_disabled":
        platforms[0].is_active = False
    elif change == "platform_deleted":
        platforms[0].deleted_at = now
    elif change == "platform_moved":
        platforms[0].project_id = companies[1].id
    elif change == "visitor_reassigned_platform":
        visitor.platform_id = platforms[1].id
    elif change == "company_deleted":
        companies[0].deleted_at = now
    elif change == "token_expired":
        token = issue_visitor_im_token(visitor, timedelta(seconds=-60))
    elif change == "wrong_uid":
        uid = f"{visitors[1].id}-vtr"
    db.commit()
    with pytest.raises(TGOAPIException) as denied:
        authenticate_im_actor(db, uid, token)
    assert denied.value.status_code == 401


def test_im_visitor_credentials_cannot_log_in_as_staff(records):
    db, _, staff, _, visitors = records
    token = visitor_token(visitors[0])
    assert resolve_staff_token(db, token) is None
    for uid in (f"{staff[0].id}-staff", "malformed", f"{uuid4()}-vtr"):
        with pytest.raises(TGOAPIException):
            authenticate_im_actor(db, uid, token)


def test_wrong_audience_and_signature_are_rejected(records):
    db, _, _, _, visitors = records
    visitor = visitors[0]
    token = visitor_token(visitor)
    claims = jwt.get_unverified_claims(token)
    wrong_audience = jwt.encode(
        {**claims, "aud": "another-purpose"},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    wrong_signature = jwt.encode(
        claims, "synthetic-invalid-key-" * 3, algorithm=settings.ALGORITHM
    )
    for credential in (wrong_audience, wrong_signature, "", "invalid"):
        with pytest.raises(TGOAPIException):
            authenticate_im_actor(db, f"{visitor.id}-vtr", credential)


@pytest.mark.parametrize("change", ["deleted", "moved", "expired"])
def test_staff_reauthentication_rejects_revoked_access(records, change):
    db, companies, staff, _, _ = records
    token = staff_token(staff[0])
    uid = f"{staff[0].id}-staff"
    if change == "deleted":
        staff[0].deleted_at = datetime.now(timezone.utc)
    elif change == "moved":
        staff[0].project_id = companies[1].id
    else:
        token = create_access_token(
            staff[0].username,
            staff[0].project_id,
            expires_delta=timedelta(seconds=-60),
        )
    db.commit()
    with pytest.raises(TGOAPIException) as denied:
        authenticate_im_actor(db, uid, token)
    assert denied.value.status_code == 401
