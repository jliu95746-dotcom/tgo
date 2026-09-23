"""Seats count enabled humans and live reservations, never reception status."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Project, Staff
from app.models.company_account import CompanyAccount, EmailAction, EmailOutbox
from app.models.company_invitation import CompanyInvitation
from app.services.company_membership import seat_usage, ensure_seat, protect_last_admin, validate_human_role_change


@pytest.fixture
def members():
    engine = create_engine("sqlite://")
    for model in (Project, Staff, CompanyAccount, EmailAction, EmailOutbox, CompanyInvitation):
        model.__table__.create(engine)
    with Session(engine) as db:
        project = Project(name="seat fixture", api_key="fixture-only")
        db.add(project)
        db.flush()
        account = CompanyAccount(project_id=project.id, status="trial", seat_limit=3,
                                 expires_at=datetime.now(timezone.utc) + timedelta(days=7))
        admin = Staff(project_id=project.id, username="admin@example.com",
                      password_hash="unused", role="admin", is_active=False)
        db.add_all([account, admin])
        db.commit()
        yield db, account, admin
    engine.dispose()


def test_admin_and_paused_humans_count_but_ai_does_not(members):
    db, account, admin = members
    db.add_all([
        Staff(project_id=admin.project_id, username="staff", password_hash="unused", service_paused=True),
        Staff(project_id=admin.project_id, username="agent", password_hash="unused", role="agent"),
        Staff(project_id=admin.project_id, username="disabled", password_hash="unused", account_enabled=False),
    ])
    db.commit()
    assert seat_usage(db, account.project_id) == (2, 0)
    ensure_seat(db, account)
    account.seat_limit = 2
    with pytest.raises(HTTPException) as error:
        ensure_seat(db, account)
    assert error.value.status_code == 409


def test_last_enabled_admin_cannot_be_removed(members):
    db, _, admin = members
    with pytest.raises(HTTPException):
        protect_last_admin(db, admin)
    second = Staff(project_id=admin.project_id, username="admin2", password_hash="unused", role="admin")
    db.add(second)
    db.commit()
    protect_last_admin(db, admin)
    second.account_enabled = False
    db.commit()
    with pytest.raises(HTTPException):
        protect_last_admin(db, admin)


@pytest.mark.parametrize("old,new", [("user", "agent"), ("agent", "user"), ("agent", "admin")])
def test_human_ai_conversion_cannot_bypass_seats(members, old, new):
    _, _, staff = members
    staff.role = old
    with pytest.raises(HTTPException) as error:
        validate_human_role_change(staff, new)
    assert error.value.status_code == 409


def test_human_role_change_preserves_account_category(members):
    _, _, staff = members
    validate_human_role_change(staff, "user")
