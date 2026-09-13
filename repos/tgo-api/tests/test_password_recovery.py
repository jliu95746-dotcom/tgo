"""Recovery is an operator-only action scoped to an explicitly confirmed account."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Project, Staff
from app.services import password_recovery
from scripts.reset_staff_password import run_recovery


@pytest.fixture
def recovery_db(monkeypatch):
    engine = create_engine("sqlite://")
    Project.__table__.create(engine)
    Staff.__table__.create(engine)
    with Session(engine) as db:
        projects = [
            Project(name=f"项目{index}", api_key=f"key{index}") for index in range(2)
        ]
        db.add_all(projects)
        db.flush()
        accounts = [
            Staff(
                project_id=project.id,
                username=f"owner{index}@example.com",
                password_hash="old-hash",
                role="admin",
                status="offline",
            )
            for index, project in enumerate(projects)
        ]
        db.add_all(accounts)
        db.commit()
        monkeypatch.setattr(
            password_recovery, "get_password_hash", lambda value: "hash:" + value
        )
        yield db, projects, accounts
    engine.dispose()


def test_lookup_accepts_email_case_but_not_partial_match(recovery_db):
    db, projects, accounts = recovery_db
    target = password_recovery.find_recovery_target(db, " OWNER0@example.com ")
    assert target.staff_id == accounts[0].id and target.project_id == projects[0].id
    with pytest.raises(ValueError):
        password_recovery.find_recovery_target(db, "owner")


def test_reset_only_changes_the_confirmed_account(recovery_db):
    db, projects, accounts = recovery_db
    password_recovery.reset_confirmed_password(
        db, accounts[0].id, projects[0].id, "New-password-123"
    )
    db.refresh(accounts[0])
    db.refresh(accounts[1])
    assert accounts[0].password_hash == "hash:New-password-123"
    assert accounts[1].password_hash == "old-hash"


def test_mismatched_project_is_rejected_without_writes(recovery_db):
    db, projects, accounts = recovery_db
    with pytest.raises(ValueError):
        password_recovery.reset_confirmed_password(
            db, accounts[0].id, projects[1].id, "New-password-123"
        )
    assert all(account.password_hash == "old-hash" for account in accounts)


@pytest.mark.parametrize("password", ["short", "中" * 25])
def test_invalid_password_leaves_existing_hash_untouched(recovery_db, password):
    db, projects, accounts = recovery_db
    with pytest.raises(ValueError):
        password_recovery.reset_confirmed_password(
            db, accounts[0].id, projects[0].id, password
        )
    assert accounts[0].password_hash == "old-hash"


@pytest.mark.parametrize("removed", ["staff", "project"])
def test_removed_target_cannot_be_reset(recovery_db, removed):
    db, projects, accounts = recovery_db
    target = accounts[0] if removed == "staff" else projects[0]
    target.deleted_at = datetime.now(timezone.utc)
    db.commit()
    with pytest.raises(ValueError):
        password_recovery.find_recovery_target(db, "owner0@example.com")
    with pytest.raises(ValueError):
        password_recovery.reset_confirmed_password(
            db, accounts[0].id, projects[0].id, "New-password-123"
        )


def test_hash_failure_does_not_change_any_account(recovery_db, monkeypatch):
    db, projects, accounts = recovery_db

    def fail_hash(_password):
        raise RuntimeError("Hash unavailable")

    monkeypatch.setattr(password_recovery, "get_password_hash", fail_hash)
    with pytest.raises(RuntimeError):
        password_recovery.reset_confirmed_password(
            db, accounts[0].id, projects[0].id, "New-password-123"
        )
    assert all(account.password_hash == "old-hash" for account in accounts)


def test_interactive_cancel_never_requests_a_password(recovery_db):
    db, _, accounts = recovery_db
    answers = iter(["owner0@example.com", "wrong-project"])

    def no_password(_prompt):
        raise AssertionError("Password requested before target confirmation")

    assert (
        run_recovery(db, lambda _: next(answers), no_password, lambda _: None) is False
    )
    assert accounts[0].password_hash == "old-hash"


def test_interactive_password_mismatch_does_not_write(recovery_db):
    db, projects, accounts = recovery_db
    answers = iter(["owner0@example.com", str(projects[0].id)])
    passwords = iter(["New-password-123", "Different-password-123"])
    with pytest.raises(ValueError, match="两次密码不一致"):
        run_recovery(
            db, lambda _: next(answers), lambda _: next(passwords), lambda _: None
        )
    assert accounts[0].password_hash == "old-hash"


def test_interactive_success_never_prints_password(recovery_db):
    db, projects, accounts = recovery_db
    answers = iter(["owner0@example.com", str(projects[0].id)])
    output = []
    assert run_recovery(
        db, lambda _: next(answers), lambda _: "New-password-123", output.append
    )
    assert accounts[0].password_hash == "hash:New-password-123"
    assert "New-password-123" not in "".join(output)
