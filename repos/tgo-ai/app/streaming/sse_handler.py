"""SSE delivery with idle keepalives and deterministic response cleanup."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Callable, Mapping
from time import monotonic

from anyio import CancelScope
from fastapi import Request
from starlette.background import BackgroundTask
from starlette.responses import ContentStream, StreamingResponse
from starlette.types import Receive, Scope, Send

from app.core.logging import get_logger
from app.models.streaming import EventType, StreamingEvent

from .event_emitter import StreamingEventEmitter, cleanup_event_emitter

logger = get_logger(__name__)
STREAM_ERROR = "消息流暂时不可用，请重试或转人工处理"
TERMINAL_EVENTS = {
    EventType.WORKFLOW_COMPLETED,
    EventType.WORKFLOW_FAILED,
}


class SSEResponse(StreamingResponse):
    """Retain cleanup even when sending headers fails before iteration."""

    def __init__(
        self,
        generator: ContentStream,
        *,
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
        background: BackgroundTask | None = None,
        cleanup: Callable[[], None] | None = None,
    ) -> None:
        self._cleanup = cleanup
        response_headers = {
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Cache-Control",
        }
        response_headers.update(headers or {})
        super().__init__(
            generator,
            status_code=status_code,
            media_type="text/event-stream",
            headers=response_headers,
            background=background,
        )

    async def aclose(self) -> None:
        with CancelScope(shield=True):
            try:
                if isinstance(self.body_iterator, AsyncGenerator):
                    await self.body_iterator.aclose()
            finally:
                if self._cleanup is not None:
                    self._cleanup()

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.aclose()


class SSEHandler:
    """Deliver the existing event protocol without owning the producer task."""

    def __init__(
        self, event_emitter: StreamingEventEmitter, request: Request
    ) -> None:
        self.event_emitter = event_emitter
        self.request = request
        self.logger = logger.bind(
            request_id=event_emitter.request_id,
            correlation_id=event_emitter.correlation_id,
        )
        self._connected = False
        self._heartbeat_interval = 30.0

    async def stream_events(self) -> AsyncGenerator[str, None]:
        self._connected = True
        self.logger.info("SSE stream started")
        source = self._domain_events()
        try:
            yield self._format_sse_event(
                "connected",
                {
                    "message": "Stream connected",
                    "request_id": self.event_emitter.request_id,
                    "correlation_id": self.event_emitter.correlation_id,
                },
            )
            async for frame in source:
                yield frame
        except Exception as exc:
            self.logger.error(
                "SSE stream error", error_type=type(exc).__name__
            )
            yield self._format_sse_event(
                "error", {"message": STREAM_ERROR, "error": STREAM_ERROR}
            )
        finally:
            self._connected = False
            await source.aclose()
            self.logger.info("SSE stream ended")
        # Cancellation and GeneratorExit skip the normal closing frame.
        yield self._format_sse_event(
            "disconnected", {"message": "Stream disconnected"}
        )

    async def _domain_events(self) -> AsyncGenerator[str, None]:
        buffered = self.event_emitter.get_events()
        buffered_ids = {id(event) for event in buffered}
        for buffered_event in buffered:
            if not self._connected or not await self._is_client_connected():
                return
            yield self._format_sse_event(
                "event", self._event_to_payload(buffered_event)
            )
            if buffered_event.event_type in TERMINAL_EVENTS:
                return

        heartbeat_at = monotonic() + self._heartbeat_interval
        while self._connected and await self._is_client_connected():
            timeout = min(1.0, max(0.001, heartbeat_at - monotonic()))
            event = await self.event_emitter.get_next_event(timeout=timeout)
            if event is not None:
                if id(event) in buffered_ids:
                    buffered_ids.remove(id(event))
                    continue
                yield self._format_sse_event(
                    "event", self._event_to_payload(event)
                )
                if event.event_type in TERMINAL_EVENTS:
                    return
                heartbeat_at = monotonic() + self._heartbeat_interval
            elif not self.event_emitter.is_streaming_enabled():
                return
            elif monotonic() >= heartbeat_at:
                # SSE comments keep HTTP intermediaries alive without changing
                # the domain protocol. The API relay ignores comment frames.
                yield ": heartbeat\n\n"
                heartbeat_at = monotonic() + self._heartbeat_interval

    async def _is_client_connected(self) -> bool:
        try:
            return not await self.request.is_disconnected()
        except Exception:
            return False

    def _event_to_payload(self, event: StreamingEvent) -> dict[str, object]:
        # Preserve the existing timestamp/UUID encoding with json default=str.
        return event.model_dump()

    def _format_sse_event(
        self,
        event_type: str,
        data: Mapping[str, object],
        event_id: str | None = None,
    ) -> str:
        lines = [f"id: {event_id}"] if event_id else []
        lines.append(f"event: {event_type}")
        content = json.dumps(data, default=str, ensure_ascii=False)
        lines.extend(f"data: {line}" for line in content.split("\n"))
        lines.append("")
        return "\n".join(lines) + "\n"

    def disconnect(self) -> None:
        self._connected = False
        self.logger.info("SSE stream disconnected by handler")


def create_sse_response(
    event_emitter: StreamingEventEmitter, request: Request
) -> SSEResponse:
    handler = SSEHandler(event_emitter, request)
    cleaned = False

    def cleanup() -> None:
        nonlocal cleaned
        if cleaned:
            return
        cleaned = True
        handler.disconnect()
        event_emitter.remove_stream(handler)
        cleanup_event_emitter(
            event_emitter.request_id, event_emitter.correlation_id
        )

    async def content() -> AsyncGenerator[str, None]:
        source = handler.stream_events()
        try:
            async for frame in source:
                yield frame
        finally:
            with CancelScope(shield=True):
                try:
                    await source.aclose()
                finally:
                    cleanup()

    event_emitter.add_stream(handler)
    return SSEResponse(
        content(), background=BackgroundTask(cleanup), cleanup=cleanup
    )


class SSEEventSender:
    """Helper class for sending typed events to SSE streams."""

    def __init__(self, handler: SSEHandler) -> None:
        self.handler = handler
        self._queue: asyncio.Queue[StreamingEvent] = asyncio.Queue()

    def send_event(self, event: StreamingEvent) -> None:
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("SSE event queue is full, dropping event")

    async def get_next_event(
        self, timeout: float | None = None
    ) -> StreamingEvent | None:
        try:
            if timeout is not None:
                return await asyncio.wait_for(
                    self._queue.get(), timeout=timeout
                )
            return await self._queue.get()
        except asyncio.TimeoutError:
            return None
