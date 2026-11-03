"""Durable source events freeze once, recover leases, and honor revocation."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.workflow_event_subscription import WorkflowEventDelivery
from app.schemas.workflow_events import (
    WorkflowEventAccepted,
    WorkflowEventTestContext,
)
from app.services import workflow_source_dispatch as dispatch
from pydantic import SecretStr
from app.core.config import settings
from app.services.workflow_event_crypto import decrypt_event_payload
from tests.test_workflow_event_source_authority import (
    source,
    records,
)  # noqa: F401


@pytest.fixture
def transport(monkeypatch):
    context = AsyncMock(
        return_value=WorkflowEventTestContext(
            workflow_id="flow",
            event_type="staff.session.closed",
            release_id="release",
            release_digest="a" * 64,
            version=1,
            status="active",
        )
    )
    snapshot = AsyncMock(
        return_value={
            "snapshot_scope": "bounded_session_excerpts",
            "excerpts": [],
        }
    )
    send = AsyncMock(
        return_value=WorkflowEventAccepted(
            execution_id="execution", status="pending", deduplicated=False
        )
    )
    monkeypatch.setattr(
        dispatch.workflow_client, "get_event_test_context", context
    )
    monkeypatch.setattr(dispatch.workflow_client, "send_source_event", send)
    monkeypatch.setattr(dispatch, "load_closed_session_snapshot", snapshot)
    return context, snapshot, send


def test_source_scan_is_unique_and_does_not_backfill_before_enable(source):
    db, *_, command, sub, delivery, _query = source
    assert dispatch.collect_source_events(db) == 0
    db.delete(delivery)
    db.commit()
    assert dispatch.collect_source_events(db) == 1
    assert dispatch.collect_source_events(db) == 0
    db.delete(db.scalar(select(WorkflowEventDelivery)))
    command.created_at = sub.enabled_at - timedelta(seconds=1)
    db.commit()
    assert dispatch.collect_source_events(db) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("versioned", [False, True])
async def test_transport_uncertainty_retries_exact_frozen_body(
    source, transport, monkeypatch, versioned
):
    db, *_, delivery, _query = source
    _context, snapshot, send = transport
    if versioned:
        monkeypatch.setattr(
            settings,
            "WORKFLOW_EVENT_DELIVERY_KEYS",
            SecretStr('{"k1":"old-event-test-secret-01234567890123456789"}'),
        )
        monkeypatch.setattr(
            settings, "WORKFLOW_EVENT_DELIVERY_ACTIVE_KEY_ID", "k1"
        )
    send.side_effect = [
        HTTPException(504, "unknown"),
        WorkflowEventAccepted(
            execution_id="execution", status="pending", deduplicated=True
        ),
    ]
    claim = dispatch.claim_source_event(db)
    await dispatch.deliver_source_event(db, claim)
    db.refresh(delivery)
    assert delivery.status == "pending" and delivery.encrypted_payload
    frozen = decrypt_event_payload(delivery.encrypted_payload)
    assert "excerpts" in frozen
    if versioned:
        monkeypatch.setattr(
            settings,
            "WORKFLOW_EVENT_DELIVERY_KEYS",
            SecretStr(
                '{"k1":"old-event-test-secret-01234567890123456789","k2":"new-event-test-secret-01234567890123456789"}'
            ),
        )
        monkeypatch.setattr(
            settings, "WORKFLOW_EVENT_DELIVERY_ACTIVE_KEY_ID", "k2"
        )
    delivery.next_attempt_at = datetime.now(timezone.utc) - timedelta(
        seconds=1
    )
    db.commit()
    second = dispatch.claim_source_event(db)
    await dispatch.deliver_source_event(db, second)
    db.refresh(delivery)
    assert (
        delivery.status == "accepted" and delivery.execution_id == "execution"
    )
    assert delivery.encrypted_payload is None
    assert snapshot.await_count == 1
    assert send.call_args_list[0].args == send.call_args_list[1].args


def test_expired_lease_recovers_and_stale_claim_cannot_finish(source):
    db, *_, delivery, _query = source
    first = dispatch.claim_source_event(db)
    assert first and dispatch.claim_source_event(db) is None
    delivery.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    second = dispatch.claim_source_event(db)
    assert second and second.attempt == first.attempt + 1
    assert not dispatch.finish_source_event(
        db, first, "accepted", execution_id="stale"
    )
    assert dispatch.finish_source_event(
        db, second, "accepted", execution_id="current"
    )
    db.refresh(delivery)
    assert delivery.execution_id == "current"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["publication", "subscription", "admin"])
async def test_revoked_binding_never_reads_or_sends_customer_excerpt(
    source, transport, change
):
    db, staff, *_, sub, delivery, _query = source
    context, snapshot, send = transport
    claim = dispatch.claim_source_event(db)
    if change == "publication":
        context.return_value = context.return_value.model_copy(
            update={"release_digest": "b" * 64}
        )
    elif change == "subscription":
        sub.enabled = False
    else:
        staff.account_enabled = False
    db.commit()
    await dispatch.deliver_source_event(db, claim)
    db.refresh(delivery)
    assert (
        delivery.status == "cancelled" and delivery.encrypted_payload is None
    )
    snapshot.assert_not_awaited()
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_snapshot_expiry_finishes_without_network_or_plaintext_retention(
    source, transport
):
    db, *_, delivery, _query = source
    delivery.created_at = datetime.now(timezone.utc) - timedelta(minutes=31)
    delivery.encrypted_payload = "do-not-read"
    db.commit()
    assert dispatch.claim_source_event(db) is None
    db.refresh(delivery)
    assert delivery.status == "failed" and delivery.encrypted_payload is None
    assert delivery.last_error == "delivery_expired"


@pytest.mark.asyncio
async def test_missing_historical_cipher_key_retains_frozen_input_for_recovery(
    source, transport, monkeypatch
):
    db, *_, delivery, _query = source
    _context, snapshot, send = transport
    monkeypatch.setattr(
        settings,
        "WORKFLOW_EVENT_DELIVERY_KEYS",
        SecretStr('{"k1":"old-event-test-secret-01234567890123456789"}'),
    )
    monkeypatch.setattr(
        settings, "WORKFLOW_EVENT_DELIVERY_ACTIVE_KEY_ID", "k1"
    )
    send.side_effect = HTTPException(504, "unknown")
    await dispatch.deliver_source_event(db, dispatch.claim_source_event(db))
    db.refresh(delivery)
    frozen = delivery.encrypted_payload
    monkeypatch.setattr(
        settings,
        "WORKFLOW_EVENT_DELIVERY_KEYS",
        SecretStr('{"k2":"new-event-test-secret-01234567890123456789"}'),
    )
    monkeypatch.setattr(
        settings, "WORKFLOW_EVENT_DELIVERY_ACTIVE_KEY_ID", "k2"
    )
    delivery.next_attempt_at = datetime.now(timezone.utc) - timedelta(
        seconds=1
    )
    db.commit()
    await dispatch.deliver_source_event(db, dispatch.claim_source_event(db))
    db.refresh(delivery)
    assert (
        delivery.status == "pending" and delivery.encrypted_payload == frozen
    )
    assert delivery.last_error == "event_delivery_key_unavailable"
    assert snapshot.await_count == 1 and send.await_count == 1
    monkeypatch.setattr(
        settings,
        "WORKFLOW_EVENT_DELIVERY_KEYS",
        SecretStr(
            '{"k1":"old-event-test-secret-01234567890123456789","k2":"new-event-test-secret-01234567890123456789"}'
        ),
    )
    send.side_effect = None
    delivery.next_attempt_at = datetime.now(timezone.utc) - timedelta(
        seconds=1
    )
    db.commit()
    await dispatch.deliver_source_event(db, dispatch.claim_source_event(db))
    db.refresh(delivery)
    assert delivery.status == "accepted" and delivery.encrypted_payload is None
    assert snapshot.await_count == 1
    assert send.call_args_list[0].args == send.call_args_list[1].args
