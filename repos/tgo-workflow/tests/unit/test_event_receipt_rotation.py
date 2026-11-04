"""Committed historical events remain idempotent after key and input changes."""

import json

import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from app.config import settings
from app.models.execution import WorkflowExecution
from app.services import event_trigger
from tests.unit.test_event_source_binding import (
    source_store,
    request_for,
    binding,
)  # noqa: F401

OLD = "old-receipt-material-for-tests-0123456789"
NEW = "new-receipt-material-for-tests-0123456789"


def configure(monkeypatch, keys=None, active=None):
    monkeypatch.setattr(
        settings,
        "WORKFLOW_EVENT_RECEIPT_KEYS",
        SecretStr(json.dumps(keys)) if keys else None,
    )
    monkeypatch.setattr(
        settings, "WORKFLOW_EVENT_RECEIPT_ACTIVE_KEY_ID", active
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_retries_after_rotation_and_terminal_scrub_use_saved_key(
    source_store, monkeypatch, legacy
):
    sessions, project, workflow, context, verify = source_store
    configure(
        monkeypatch, None if legacy else {"r1": OLD}, None if legacy else "r1"
    )
    source = binding()
    request = request_for(project, context, source)
    event_id = f"source-{source.delivery_id}"
    async with sessions() as db:
        accepted = await event_trigger.accept_event(
            db, workflow, request, event_id, OLD
        )
        row = await db.get(WorkflowExecution, accepted.execution_id)
        assert row.event_receipt_key_id == (None if legacy else "r1")
        row.status, row.input = "completed", None
        await db.commit()
        keys = {"legacy" if legacy else "r1": OLD, "r2": NEW}
        configure(monkeypatch, keys, "r2")
        duplicate = await event_trigger.accept_event(
            db, workflow, request, event_id, "rotated-internal-key"
        )
        assert (
            duplicate.deduplicated
            and duplicate.execution_id == accepted.execution_id
        )
        assert verify.await_count == 1
        changed = request.model_copy(update={"data": {"different": "payload"}})
        with pytest.raises(HTTPException) as conflict:
            await event_trigger.accept_event(
                db, workflow, changed, event_id, NEW
            )
        assert conflict.value.status_code == 409
        configure(monkeypatch, {"r2": NEW}, "r2")
        with pytest.raises(HTTPException) as unavailable:
            await event_trigger.accept_event(
                db, workflow, request, event_id, NEW
            )
        assert unavailable.value.status_code == 503
        assert OLD not in str(unavailable.value.detail)
        event_trigger.enqueue_pending_initial.assert_awaited_once()


@pytest.mark.asyncio
async def test_unique_conflict_rechecks_winning_rows_key_not_new_active_key(
    source_store, monkeypatch
):
    sessions, project, workflow, context, _verify = source_store
    configure(monkeypatch, {"r1": OLD}, "r1")
    request = request_for(project, context)
    async with sessions() as db:
        first = await event_trigger.accept_event(
            db, workflow, request, "race-event", OLD
        )
    configure(monkeypatch, {"r1": OLD, "r2": NEW}, "r2")
    original = event_trigger._saved_event
    calls = 0

    async def missed_initial_read(db, execution_id, project_id):
        nonlocal calls
        calls += 1
        return (
            None
            if calls == 1
            else await original(db, execution_id, project_id)
        )

    monkeypatch.setattr(event_trigger, "_saved_event", missed_initial_read)
    async with sessions() as db:
        retried = await event_trigger.accept_event(
            db, workflow, request, "race-event", NEW
        )
        assert (
            retried.deduplicated and retried.execution_id == first.execution_id
        )
        assert calls == 2
    event_trigger.enqueue_pending_initial.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_keyring_is_not_reported_as_ready_publication(
    source_store, monkeypatch
):
    sessions, project, workflow, _context, _verify = source_store
    configure(monkeypatch, {"r1": "invalid-private-material"}, "r1")
    async with sessions() as db:
        with pytest.raises(HTTPException) as error:
            await event_trigger.event_test_context(db, workflow, project)
    assert error.value.status_code == 503
    assert "invalid-private-material" not in str(error.value.detail)
