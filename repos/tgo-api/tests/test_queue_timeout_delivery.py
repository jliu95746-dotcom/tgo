"""Timeout notices keep delivery evidence and never replay an uncertain send."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.schemas.queue_timeout import QUEUE_TIMEOUT_NOTICE_KEY, QUEUE_TIMEOUT_TEXT
from app.services import queue_lifecycle, queue_timeout_delivery as delivery
from tests.test_queue_lifecycle import queue_db, seed_queue  # noqa: F401


@pytest.fixture
def transport(monkeypatch):
    external = AsyncMock(return_value=httpx.Response(200, json={"ok": True}))
    history = AsyncMock(return_value=SimpleNamespace(message_id="fixture-message"))
    lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(delivery, "forward_platform_message", external)
    monkeypatch.setattr(delivery.wukongim_client, "send_message", history)
    monkeypatch.setattr(
        delivery.wukongim_client, "get_message_by_client_msg_no", lookup
    )
    monkeypatch.setattr(delivery.wukongim_client, "enabled", True)
    return external, history, lookup


def expired(db, platform_type="website"):
    visitor, session, entry = seed_queue(db, platform_type=platform_type)
    queue_lifecycle.expire_entry(db, entry.id)
    db.commit()
    return visitor, session, entry


def due(db, entry):
    metadata = {**entry.extra_metadata}
    metadata[QUEUE_TIMEOUT_NOTICE_KEY] = {
        **metadata[QUEUE_TIMEOUT_NOTICE_KEY],
        "next_retry_at": (datetime.utcnow() - timedelta(seconds=1)).isoformat(),
    }
    entry.extra_metadata = metadata
    db.commit()


def notice(entry):
    return entry.extra_metadata[QUEUE_TIMEOUT_NOTICE_KEY]


@pytest.mark.asyncio
async def test_website_timeout_is_a_system_message_and_sends_once(queue_db, transport):
    external, history, lookup = transport
    visitor, session, entry = expired(queue_db)

    async def check_intent(**kwargs):
        assert notice(entry)["history_status"] == "sending"
        assert kwargs["from_uid"] == "system"
        assert kwargs["payload"]["type"] == 1005
        assert kwargs["payload"]["content"] == QUEUE_TIMEOUT_TEXT
        return SimpleNamespace(message_id="fixture-message")

    history.side_effect = check_intent
    await delivery.deliver_timeout_notice(queue_db, entry.id)
    due(queue_db, entry)
    await delivery.deliver_timeout_notice(queue_db, entry.id)
    assert notice(entry)["history_status"] == "sent"
    assert history.await_count == 1
    external.assert_not_awaited()


@pytest.mark.asyncio
async def test_external_timeout_reuses_transport_as_system_then_records_history(
    queue_db, transport
):
    external, history, lookup = transport
    visitor, session, entry = expired(queue_db, "custom")
    await delivery.deliver_timeout_notice(queue_db, entry.id)
    args, kwargs = external.await_args
    assert kwargs["from_uid"] == "system"
    assert args[0].visitor_id == visitor.id
    assert args[1] == {"type": 1, "content": QUEUE_TIMEOUT_TEXT}
    assert notice(entry)["external_status"] == notice(entry)["history_status"] == "sent"
    due(queue_db, entry)
    await delivery.recover_timeout_notices(queue_db)
    assert external.await_count == history.await_count == 1


@pytest.mark.asyncio
async def test_unknown_external_outcome_never_retries_or_claims_history(
    queue_db, transport
):
    external, history, lookup = transport
    external.side_effect = httpx.ReadTimeout("private address should never be stored")
    visitor, session, entry = expired(queue_db, "custom")
    await delivery.deliver_timeout_notice(queue_db, entry.id)
    due(queue_db, entry)
    await delivery.recover_timeout_notices(queue_db)
    await delivery.deliver_timeout_notice(queue_db, entry.id)
    assert notice(entry)["external_status"] == "unknown"
    assert notice(entry)["external_error"] == "ReadTimeout"
    assert external.await_count == 1
    history.assert_not_awaited()


@pytest.mark.asyncio
async def test_definite_connection_failure_can_recover_after_restart(
    queue_db, transport
):
    external, history, lookup = transport
    external.side_effect = [
        httpx.ConnectError("refused"),
        httpx.Response(200, json={"ok": True}),
    ]
    visitor, session, entry = expired(queue_db, "custom")
    await delivery.deliver_timeout_notice(queue_db, entry.id)
    assert notice(entry)["external_status"] == "pending"
    due(queue_db, entry)
    await delivery.recover_timeout_notices(queue_db)
    assert notice(entry)["external_status"] == "sent"
    assert external.await_count == 2 and history.await_count == 1


@pytest.mark.asyncio
async def test_lost_im_receipt_reconciles_without_resending(queue_db, transport):
    external, history, lookup = transport
    history.side_effect = httpx.ReadTimeout("lost receipt")
    visitor, session, entry = expired(queue_db)
    await delivery.deliver_timeout_notice(queue_db, entry.id)
    assert notice(entry)["history_status"] == "unknown"
    payload = history.await_args.kwargs["payload"]
    lookup.return_value = SimpleNamespace(from_uid="system", payload=payload)
    due(queue_db, entry)
    await delivery.recover_timeout_notices(queue_db)
    assert notice(entry)["history_status"] == "sent"
    assert history.await_count == 1 and lookup.await_count == 1


@pytest.mark.asyncio
async def test_missing_or_unavailable_im_lookup_does_not_resend_unknown_notice(
    queue_db, transport
):
    external, history, lookup = transport
    history.side_effect = httpx.ReadTimeout("unknown")
    visitor, session, entry = expired(queue_db)
    await delivery.deliver_timeout_notice(queue_db, entry.id)
    for result in [None, httpx.ConnectError("offline")]:
        lookup.side_effect = result
        due(queue_db, entry)
        await delivery.deliver_timeout_notice(queue_db, entry.id)
        assert notice(entry)["history_status"] == "unknown"
    assert history.await_count == 1


@pytest.mark.asyncio
async def test_reopened_conversation_cancels_an_unsent_notice(queue_db, transport):
    external, history, lookup = transport
    visitor, session, entry = expired(queue_db, "custom")
    visitor.service_status = "active"
    queue_db.commit()
    await delivery.deliver_timeout_notice(queue_db, entry.id)
    assert notice(entry)["external_status"] == "cancelled"
    assert notice(entry)["history_status"] == "cancelled"
    external.assert_not_awaited()
    history.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_expiries_without_a_notice_are_not_backfilled(queue_db, transport):
    external, history, lookup = transport
    visitor, session, entry = seed_queue(queue_db, status="expired")
    await delivery.deliver_timeout_notice(queue_db, entry.id)
    await delivery.recover_timeout_notices(queue_db)
    external.assert_not_awaited()
    history.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_is_project_scoped(queue_db, transport):
    external, history, lookup = transport
    first = expired(queue_db)[2]
    second = expired(queue_db)[2]
    due(queue_db, first)
    due(queue_db, second)
    await delivery.recover_timeout_notices(queue_db, project_id=first.project_id)
    assert notice(first)["history_status"] == "sent"
    assert notice(second)["history_status"] == "pending"
    assert history.await_count == 1
