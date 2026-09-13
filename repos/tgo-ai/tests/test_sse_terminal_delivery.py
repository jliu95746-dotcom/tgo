"""Buffered and queued copies must not replay progress or terminal events."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.streaming import BaseEventData, EventType
from app.streaming.event_emitter import StreamingEventEmitter
from app.streaming.sse_handler import SSEHandler


@pytest.mark.parametrize(
    "terminal", [EventType.WORKFLOW_FAILED, EventType.WORKFLOW_COMPLETED]
)
@pytest.mark.parametrize("buffered_terminal", [False, True])
async def test_terminal_is_delivered_once_without_replaying_buffer(
    terminal, buffered_terminal
):
    emitter = StreamingEventEmitter("owned", "owned")
    emitter.enable_streaming()
    emitter.emit(EventType.WORKFLOW_STARTED, BaseEventData())
    if buffered_terminal:
        emitter.emit(terminal, BaseEventData())
    handler = SSEHandler(
        emitter, SimpleNamespace(is_disconnected=AsyncMock(return_value=False))
    )
    payloads = []
    async for chunk in handler.stream_events():
        if chunk.startswith("event: event\n"):
            payload = json.loads(chunk.split("data: ", 1)[1].strip())
            payloads.append(payload["event_type"])
            if len(payloads) == 1 and not buffered_terminal:
                emitter.emit(terminal, BaseEventData())
    assert payloads == ["workflow_started", terminal.value]
