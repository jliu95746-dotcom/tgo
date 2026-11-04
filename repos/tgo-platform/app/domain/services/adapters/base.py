from __future__ import annotations
from abc import ABC
from app.domain.entities import StreamEvent
from app.domain.ports import PlatformAdapter
import hashlib
import json


class DeliveryRejected(ValueError):
    """Validation failed before any outbound request was made."""


def provider_receipt(provider: str, value: object) -> str:
    if not isinstance(value, dict) or not value:
        raise ValueError("Provider did not return a verifiable acknowledgement")
    identifier = value.get("msgid") or value.get("message_id") or value.get("ts")
    for key in ("data", "result"):
        nested = value.get(key)
        if isinstance(nested, dict):
            identifier = identifier or nested.get("message_id") or nested.get("msgid")
    if identifier:
        return provider + ":" + str(identifier)[:400]
    digest = hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return provider + ":ack:" + digest


class BasePlatformAdapter(PlatformAdapter, ABC):
    supports_stream: bool = False
    delivery_receipt: str | None = None


class SimpleStdoutAdapter(BasePlatformAdapter):
    """Minimal adapter for MVP/testing. Prints incremental/final outputs."""

    async def send_incremental(self, ev: StreamEvent) -> None:  # pragma: no cover - side effect
        print(f"[INCR] event={ev.event} payload={ev.payload}")

    async def send_final(self, content: dict) -> None:  # pragma: no cover - side effect
        print(f"[FINAL] content={content}")

