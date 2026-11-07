"""Validate receipts for the existing provider/default-model sync clients."""

from collections.abc import Sequence
from typing import TypeAlias

import httpx
from pydantic import TypeAdapter, ValidationError

from app.services.ai_transport import JsonObject

SyncResult: TypeAlias = tuple[bool, str | None, JsonObject | None]
_object_response = TypeAdapter(JsonObject)


def read_sync_receipt(
    response: httpx.Response,
    expected_ids: Sequence[str],
    identity_field: str,
) -> SyncResult:
    """Only acknowledge the complete requested batch, regardless of order.

    The AI service returns provider IDs or project IDs in ``data``. Other
    fields remain forward compatible; response bodies never become errors.
    """
    if not 200 <= response.status_code < 300:
        return False, f"http_error: {response.status_code}", None
    try:
        data = _object_response.validate_python(response.json())
    except (ValueError, ValidationError):
        return False, "invalid_sync_response", None
    rows = data.get("data")
    if not isinstance(rows, list) or len(rows) != len(expected_ids):
        return False, "invalid_sync_response", None
    received: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            return False, "invalid_sync_response", None
        identity = row.get(identity_field)
        if not isinstance(identity, str) or not identity:
            return False, "invalid_sync_response", None
        received.append(identity)
    if (len(set(received)) != len(received)
            or sorted(received) != sorted(expected_ids)):
        return False, "invalid_sync_response", None
    return True, None, data
