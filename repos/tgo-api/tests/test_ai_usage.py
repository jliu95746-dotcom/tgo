"""Quota ordering and retry protection use durable rows, not cache counters."""

from datetime import datetime, timedelta, timezone

import pytest

from app.core.exceptions import TGOAPIException
from app.core.config import settings
from app.models.ai_usage import AIUsageMovement, AIUsageReservation
from app.models.company_account import AICreditBatch, CompanyAccount
from app.services.ai_usage import begin_publication, release, reserve, settle
from tests.test_billing_quotes import commercial_company  # noqa: F401


def prepare(fixture):
    db, staff, _, account, _ = fixture
    now = datetime.now(timezone.utc)
    account.expires_at = now + timedelta(days=7)
    for model in (AICreditBatch, AIUsageReservation, AIUsageMovement):
        model.__table__.create(db.get_bind())
    batch = AICreditBatch(
        project_id=staff.project_id,
        source_key="synthetic-credit",
        kind="trial",
        amount=1,
        remaining=1,
        expires_at=account.expires_at,
    )
    db.add(batch)
    db.commit()
    return db, staff, batch


def test_last_credit_cannot_be_reserved_twice(commercial_company):
    db, staff, batch = prepare(commercial_company)
    first = reserve(db, staff.project_id, "round-1")
    db.commit()
    with pytest.raises(TGOAPIException) as error:
        reserve(db, staff.project_id, "round-2")
    assert error.value.code == "AI_QUOTA_EXHAUSTED"
    assert batch.remaining == 0
    release(db, staff.project_id, first.id, first.lease_id)
    db.commit()
    assert batch.remaining == 1
    release(db, staff.project_id, first.id, first.lease_id)
    db.commit()
    assert batch.remaining == 1


def test_repeated_completion_never_deducts_twice(commercial_company):
    db, staff, batch = prepare(commercial_company)
    row = reserve(db, staff.project_id, "round-1")
    db.commit()
    begin_publication(
        db, staff.project_id, row.id, row.lease_id, {"channel": "synthetic"}
    )
    db.commit()
    settle(db, staff.project_id, row.id, row.lease_id)
    db.commit()
    settle(db, staff.project_id, row.id, row.lease_id)
    release(db, staff.project_id, row.id, row.lease_id)
    db.commit()
    assert batch.remaining == 0
    assert db.query(AIUsageMovement).count() == 2
    with pytest.raises(TGOAPIException):
        reserve(db, staff.project_id, "round-1")


def test_ambiguous_send_keeps_a_reconcilable_record(commercial_company):
    db, staff, batch = prepare(commercial_company)
    row = reserve(db, staff.project_id, "round-1")
    db.commit()
    begin_publication(
        db, staff.project_id, row.id, row.lease_id, {"client_msg_no": "synthetic"}
    )
    db.commit()
    release(db, staff.project_id, row.id, row.lease_id)
    db.commit()
    assert row.status == "review" and batch.remaining == 0
    settle(db, staff.project_id, row.id, row.lease_id)
    db.commit()
    assert row.status == "settled"


def test_old_worker_cannot_publish_after_reservation_retry(commercial_company):
    db, staff, _ = prepare(commercial_company)
    row = reserve(db, staff.project_id, "round-1")
    db.commit()
    old_lease = row.lease_id
    release(db, staff.project_id, row.id, old_lease)
    db.commit()
    retried = reserve(db, staff.project_id, "round-1")
    db.commit()
    assert retried.id == row.id and retried.lease_id != old_lease
    with pytest.raises(TGOAPIException):
        begin_publication(db, staff.project_id, row.id, old_lease, {})


def test_suspended_company_cannot_publish_reserved_reply(commercial_company):
    db, staff, batch = prepare(commercial_company)
    row = reserve(db, staff.project_id, "suspended-reply")
    db.commit()
    account = db.get(CompanyAccount, staff.project_id)
    account.status = "suspended"
    db.commit()
    with pytest.raises(TGOAPIException) as error:
        begin_publication(db, staff.project_id, row.id, row.lease_id, {})
    assert error.value.code == "SUBSCRIPTION_EXPIRED"
    release(db, staff.project_id, row.id, row.lease_id)
    db.commit()
    assert batch.remaining == 1


def test_concurrency_ceiling_does_not_consume_extra_credit(
    commercial_company, monkeypatch
):
    db, staff, batch = prepare(commercial_company)
    batch.amount = batch.remaining = 10
    db.commit()
    monkeypatch.setattr(settings, "SAAS_AI_MAX_CONCURRENT_REPLIES", 1)
    reserve(db, staff.project_id, "first")
    db.commit()
    with pytest.raises(TGOAPIException) as error:
        reserve(db, staff.project_id, "second")
    assert error.value.code == "AI_CONCURRENCY_EXCEEDED"
    assert batch.remaining == 9
