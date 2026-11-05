"""Real supervisor SSE transport must survive idle, errors and client stops."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from app.models.streaming import BaseEventData, EventType
from app.streaming.event_emitter import (
    StreamingEventEmitter,
    get_active_emitters,
    get_event_emitter,
)
from app.streaming.sse_handler import SSEHandler, create_sse_response
from app.streaming.owned_response import own_execution


def handler_for(emitter: StreamingEventEmitter) -> SSEHandler:
    return SSEHandler(
        emitter, SimpleNamespace(is_disconnected=AsyncMock(return_value=False))
    )


async def close_stream(stream: object) -> None:
    # Old implementation may illegally yield while handling GeneratorExit.
    # A second close clears that suspended fixture after the primary assertion.
    try:
        await stream.aclose()
    except RuntimeError:
        await stream.aclose()


@pytest.mark.asyncio
async def test_consumer_close_does_not_yield_during_generator_exit() -> None:
    emitter = StreamingEventEmitter("request-close", "correlation-close")
    emitter.enable_streaming()
    handler = handler_for(emitter)
    source = handler.stream_events()
    try:
        assert (await anext(source)).startswith("event: connected\n")
        await source.aclose()
        assert handler._connected is False
    finally:
        await close_stream(source)


@pytest.mark.asyncio
async def test_cancelled_wait_propagates_original_cancellation() -> None:
    emitter = StreamingEventEmitter("request-cancel", "correlation-cancel")
    emitter.enable_streaming()
    entered = asyncio.Event()
    original = emitter.get_next_event

    async def wait_for_event(timeout: float | None = None):
        entered.set()
        return await original(timeout)

    emitter.get_next_event = wait_for_event
    handler = handler_for(emitter)
    source = handler.stream_events()
    pending = None
    try:
        await anext(source)
        pending = asyncio.create_task(anext(source))
        await asyncio.wait_for(entered.wait(), timeout=1)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert handler._connected is False
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await close_stream(source)


@pytest.mark.asyncio
async def test_idle_connection_emits_comment_heartbeat() -> None:
    emitter = StreamingEventEmitter("request-idle", "correlation-idle")
    emitter.enable_streaming()
    handler = handler_for(emitter)
    handler._heartbeat_interval = 0.02
    source = handler.stream_events()
    try:
        await anext(source)
        frame = await asyncio.wait_for(anext(source), timeout=0.25)
        assert frame == ": heartbeat\n\n"
        # The keepalive is not a workflow event or completion.
        emitter.emit(EventType.WORKFLOW_COMPLETED, BaseEventData())
        remaining = [item async for item in source]
        assert sum("workflow_completed" in item for item in remaining) == 1
        assert remaining[-1].startswith("event: disconnected\n")
    finally:
        await close_stream(source)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["queue", "buffer"])
async def test_errors_do_not_expose_exception_details(
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    private = "fixture-customer-content-and-credential"
    emitter = StreamingEventEmitter("request-failure", "correlation-failure")
    emitter.enable_streaming()
    handler = handler_for(emitter)
    if failure == "buffer":
        emitter.emit(EventType.WORKFLOW_STARTED, BaseEventData())

        def broken_payload(event):
            raise RuntimeError(private)

        monkeypatch.setattr(handler, "_event_to_payload", broken_payload)
    else:
        monkeypatch.setattr(
            emitter,
            "get_next_event",
            AsyncMock(side_effect=RuntimeError(private)),
        )
    frames = [item async for item in handler.stream_events()]
    assert private not in "".join(frames)
    assert sum(item.startswith("event: error\n") for item in frames) == 1
    assert frames[-1].startswith("event: disconnected\n")
    captured = capsys.readouterr()
    assert private not in captured.out + captured.err + caplog.text


@pytest.mark.asyncio
async def test_closing_response_iterator_releases_registered_emitter() -> None:
    key = str(uuid4())
    emitter = get_event_emitter(key, key)
    emitter.enable_streaming()
    response = create_sse_response(
        emitter, SimpleNamespace(is_disconnected=AsyncMock(return_value=False))
    )
    source = response.body_iterator
    try:
        await anext(source)
        await source.aclose()
        assert key + ":" + key not in get_active_emitters()
        assert emitter.get_stream_stats()["active_streams"] == 0
        assert emitter.is_streaming_enabled() is False
        assert response.background is not None
        # Repeated cleanup by the response owner remains safe.
        await response.background()
    finally:
        await close_stream(source)
        if response.background is not None:
            await response.background()


@pytest.mark.asyncio
async def test_send_failure_cleans_emitter_before_body_starts() -> None:
    key = str(uuid4())
    emitter = get_event_emitter(key, key)
    emitter.enable_streaming()
    response = create_sse_response(
        emitter, SimpleNamespace(is_disconnected=AsyncMock(return_value=False))
    )

    async def send(message):
        raise RuntimeError("fixture-network-send-failure")

    async def receive():
        await asyncio.Future()

    scope = {"type": "http", "asgi": {"spec_version": "2.4"}}
    try:
        with pytest.raises(RuntimeError, match="fixture-network-send-failure"):
            await response(scope, receive, send)
        assert key + ":" + key not in get_active_emitters()
        assert emitter.get_stream_stats()["active_streams"] == 0
    finally:
        if response.background is not None:
            await response.background()


@pytest.mark.asyncio
async def test_owned_reply_keeps_cleanup_on_send_failure() -> None:
    key = str(uuid4())
    emitter = get_event_emitter(key, key)
    emitter.enable_streaming()
    source = create_sse_response(
        emitter, SimpleNamespace(is_disconnected=AsyncMock(return_value=False))
    )
    entered, stopped = asyncio.Event(), asyncio.Event()

    async def operation():
        try:
            entered.set()
            await asyncio.Future()
        finally:
            stopped.set()

    execution = asyncio.create_task(operation())
    await entered.wait()
    response = own_execution(source, execution)

    async def send(message):
        raise RuntimeError("fixture-send-failed")

    async def receive():
        await asyncio.Future()

    try:
        with pytest.raises(RuntimeError, match="fixture-send-failed"):
            await response(
                {"type": "http", "asgi": {"spec_version": "2.4"}},
                receive,
                send,
            )
        assert stopped.is_set()
        assert key + ":" + key not in get_active_emitters()
    finally:
        if not execution.done():
            execution.cancel()
        await asyncio.gather(execution, return_exceptions=True)
        if source.background is not None:
            await source.background()


@pytest.mark.asyncio
async def test_owned_reply_preserves_current_response_state() -> None:
    received, after = [], []

    async def old_body():
        yield "old-body"

    async def new_body():
        yield "current-body"

    async def operation():
        await asyncio.Future()

    async def send(message):
        received.append(message)

    async def receive():
        await asyncio.Future()

    execution = asyncio.create_task(operation())
    source = StreamingResponse(old_body(), media_type="text/plain")
    wrapped = own_execution(source, execution)
    wrapped.status_code = 202
    wrapped.headers["x-current"] = "updated"
    wrapped.body_iterator = new_body()
    wrapped.background = BackgroundTask(after.append, "done")
    try:
        await wrapped(
            {"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send
        )
        assert received[0]["status"] == 202
        assert (b"x-current", b"updated") in received[0]["headers"]
        assert (
            b"".join(item.get("body", b"") for item in received)
            == b"current-body"
        )
        assert after == ["done"]
    finally:
        if not execution.done():
            execution.cancel()
        await asyncio.gather(execution, return_exceptions=True)
        await source.body_iterator.aclose()
