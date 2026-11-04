"""Preserve each event's original proof key after its input is scrubbed."""

from pydantic import JsonValue

from app.config import settings
from app.services.event_keyring import parse_event_keyring
from app.services.execution_snapshot import input_receipt_hmac


def ensure_event_receipt_keys() -> None:
    ring = parse_event_keyring(
        settings.WORKFLOW_EVENT_RECEIPT_KEYS,
        settings.WORKFLOW_EVENT_RECEIPT_ACTIVE_KEY_ID,
        "",
    )
    # HTTP ingress validates the legacy shared key separately.
    if ring.active_id is not None:
        ring.secret(ring.active_id)


def saved_event_receipt(
    inputs: dict[str, JsonValue],
    legacy_secret: str,
    execution_id: str,
    key_id: str | None,
) -> str:
    ring = parse_event_keyring(
        settings.WORKFLOW_EVENT_RECEIPT_KEYS,
        settings.WORKFLOW_EVENT_RECEIPT_ACTIVE_KEY_ID,
        legacy_secret,
    )
    scope = (
        execution_id
        if key_id is None
        else f"event-receipt:{key_id}:{execution_id}"
    )
    return input_receipt_hmac(inputs, ring.secret(key_id), scope)


def new_event_receipt(
    inputs: dict[str, JsonValue],
    legacy_secret: str,
    execution_id: str,
) -> tuple[str | None, str]:
    ring = parse_event_keyring(
        settings.WORKFLOW_EVENT_RECEIPT_KEYS,
        settings.WORKFLOW_EVENT_RECEIPT_ACTIVE_KEY_ID,
        legacy_secret,
    )
    scope = (
        execution_id
        if ring.active_id is None
        else f"event-receipt:{ring.active_id}:{execution_id}"
    )
    return ring.active_id, input_receipt_hmac(
        inputs, ring.secret(ring.active_id), scope
    )
