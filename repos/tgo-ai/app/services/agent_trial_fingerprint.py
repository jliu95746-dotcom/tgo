"""Opaque trial identity without storing credential or configuration values."""

import hashlib
import hmac
import json

from pydantic import JsonValue, TypeAdapter

from app.config import settings
from app.models.internal import Agent

json_object = TypeAdapter(dict[str, JsonValue])


def private_digest(value: dict[str, JsonValue]) -> str:
    encoded = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hmac.new(
        settings.secret_key.encode(), encoded.encode(), hashlib.sha256
    ).hexdigest()


def configuration_fingerprint(agent: Agent) -> str:
    excluded = {"name", "created_at", "updated_at", "is_default"}
    if not agent.business_connections:
        # Keep untouched knowledge-only reports compatible with the old digest.
        excluded.add("business_connections")
    if agent.device_script_selection is None:
        excluded.add("device_script_selection")
    if agent.model_owner_revision is not None:
        # The owner revision covers credentials. Read-only SaaS checks need no key.
        excluded.add("llm_provider_credentials")
    snapshot = json_object.validate_python(
        agent.model_dump(mode="json", exclude=excluded)
    )
    for field in ("collections", "tools", "workflows", "business_connections"):
        rows = snapshot.get(field)
        if isinstance(rows, list):
            snapshot[field] = sorted(
                rows, key=lambda row: json.dumps(row, sort_keys=True)
            )
    return private_digest(snapshot)
