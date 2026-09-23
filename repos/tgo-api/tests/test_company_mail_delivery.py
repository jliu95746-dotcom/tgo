"""Durable mail queue handles stale links, leases and SMTP failures safely."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.company_account import EmailAction, EmailOutbox
from app.services import company_mail_delivery as delivery
from app.services.company_email import MailPayload


@pytest.fixture
def mail_queue(monkeypatch):
    engine = create_engine("sqlite://")
    EmailAction.__table__.create(engine)
    EmailOutbox.__table__.create(engine)
    sessions = sessionmaker(engine)
    monkeypatch.setattr(delivery, "SessionLocal", sessions)
    yield sessions
    engine.dispose()


def enqueue(db: Session, *, expired=False, used=False, offset=0):
    now = datetime.now(timezone.utc)
    action = EmailAction(
        project_id=uuid4(),
        staff_id=uuid4(),
        token_hash=sha256(str(uuid4()).encode()).hexdigest(),
        purpose="verify",
        expires_at=now + timedelta(hours=-1 if expired else 1),
        used_at=now if used else None,
    )
    db.add(action)
    db.flush()
    mail = EmailOutbox(
        project_id=action.project_id,
        action_id=action.id,
        encrypted_payload="synthetic-encrypted-payload",
        available_at=now - timedelta(minutes=10) + timedelta(seconds=offset),
    )
    db.add(mail)
    db.commit()
    return mail.id


def test_stale_links_do_not_delay_next_valid_mail(mail_queue):
    with mail_queue() as db:
        stale = [
            enqueue(db, expired=index % 2 == 0, used=index % 2 == 1)
            for index in range(12)
        ]
        valid = enqueue(db, offset=60)
        claimed = delivery.claim_mail(db)
        assert claimed is not None and claimed[0] == valid
        for identifier in stale:
            mail = db.get(EmailOutbox, identifier)
            assert mail.status == "expired" and mail.encrypted_payload == ""
            assert mail.attempts == 0


def test_active_lease_is_skipped_and_expired_lease_reclaimed(mail_queue):
    with mail_queue() as db:
        first = enqueue(db)
        second = enqueue(db, offset=60)
        claim = delivery.claim_mail(db)
        assert claim[0] == first
        assert delivery.claim_mail(db)[0] == second
        assert delivery.claim_mail(db) is None
        mail = db.get(EmailOutbox, first)
        mail.locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
        reclaimed = delivery.claim_mail(db)
        assert reclaimed[0] == first and reclaimed[1] != claim[1]
        db.refresh(mail)
        assert mail.attempts == 2


def test_stale_cleanup_is_bounded_and_resumes_next_pass(mail_queue):
    with mail_queue() as db:
        for _ in range(101):
            enqueue(db, expired=True)
        valid = enqueue(db, offset=60)
        assert delivery.claim_mail(db) is None
        assert db.query(EmailOutbox).filter_by(status="expired").count() == 100
        assert delivery.claim_mail(db)[0] == valid


def fake_payload(monkeypatch):
    payload = MailPayload(
        recipient="synthetic@example.com", subject="合成验证", body="测试内容"
    )
    monkeypatch.setattr(
        delivery, "decrypt_str", lambda _: payload.model_dump_json()
    )


def test_success_clears_encrypted_link_and_lease(mail_queue, monkeypatch):
    with mail_queue() as db:
        identifier = enqueue(db)
    fake_payload(monkeypatch)
    sent = []
    monkeypatch.setattr(delivery, "send_mail", sent.append)
    assert delivery.deliver_one()
    with mail_queue() as db:
        mail = db.get(EmailOutbox, identifier)
        assert mail.status == "sent" and mail.sent_at is not None
        assert mail.encrypted_payload == "" and mail.last_error is None
        assert mail.lease_id is None and mail.locked_until is None
    assert len(sent) == 1
    assert not delivery.deliver_one()


def test_smtp_failure_retries_without_persisting_sensitive_error(
    mail_queue, monkeypatch
):
    with mail_queue() as db:
        identifier = enqueue(db)
    fake_payload(monkeypatch)

    def fail(_):
        raise TimeoutError("synthetic-secret-must-not-be-persisted")

    monkeypatch.setattr(delivery, "send_mail", fail)
    assert delivery.deliver_one()
    with mail_queue() as db:
        mail = db.get(EmailOutbox, identifier)
        assert mail.status == "pending" and mail.last_error == "TimeoutError"
        assert mail.encrypted_payload and mail.lease_id is None
        assert mail.available_at.replace(tzinfo=timezone.utc) > datetime.now(
            timezone.utc
        )
    assert not delivery.deliver_one()


def test_old_worker_cannot_overwrite_new_lease(mail_queue, monkeypatch):
    with mail_queue() as db:
        identifier = enqueue(db)
    fake_payload(monkeypatch)
    replacement = uuid4()

    def replace_lease(_):
        with mail_queue() as db:
            mail = db.get(EmailOutbox, identifier)
            mail.lease_id = replacement
            db.commit()

    monkeypatch.setattr(delivery, "send_mail", replace_lease)
    assert delivery.deliver_one()
    with mail_queue() as db:
        mail = db.get(EmailOutbox, identifier)
        assert mail.status == "pending" and mail.lease_id == replacement
        assert mail.encrypted_payload and mail.sent_at is None
