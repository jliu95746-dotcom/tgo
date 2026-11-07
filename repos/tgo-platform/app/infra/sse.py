from __future__ import annotations
import json
from typing import AsyncIterator

from app.domain.entities import StreamEvent
from app.domain.ports import SSEManager


def _json_or_text(s: str):
    try:
        return json.loads(s)
    except Exception:
        return {"text": s}


class DefaultSSEManager(SSEManager):
    async def stream_events(
        self, frames: AsyncIterator[bytes]
    ) -> AsyncIterator[StreamEvent]:
        buffer_event: str | None = None
        async for b in frames:
            line = b.decode("utf-8")
            if line.startswith("event:"):
                buffer_event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                payload_raw = line.split(":", 1)[1].strip()
                payload = _json_or_text(payload_raw)
                yield StreamEvent(event=buffer_event or "event", payload=payload)
                buffer_event = None

    async def aggregate(self, events: AsyncIterator[StreamEvent]) -> dict:
        chunks: list[str] = []
        async for ev in events:
            if ev.event in {"error", "disconnected"}:
                break
            else:
                payload = ev.payload or {}
                et = payload.get("event_type")
                if et in {"team_run_content"}:
                    data = payload.get("data", {})
                    text = data.get("content")
                    if text:
                        chunks.append(text)
                if et in {
                    "workflow_completed",
                    "team_run_completed",
                    "workflow_failed",
                }:
                    break
        return {"text": "".join(chunks)}
