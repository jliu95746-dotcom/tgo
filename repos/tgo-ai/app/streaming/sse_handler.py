"""
Server-Sent Events (SSE) handler for streaming coordination events.
"""

import asyncio
import json
from typing import AsyncGenerator, Optional

from fastapi import Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from app.core.logging import get_logger
from app.models.streaming import EventType, StreamingEvent

from .event_emitter import StreamingEventEmitter, cleanup_event_emitter

logger = get_logger(__name__)


class SSEResponse(StreamingResponse):
    """Custom StreamingResponse for Server-Sent Events."""

    def __init__(self, generator, **kwargs):
        super().__init__(
            generator,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "Cache-Control",
            },
            **kwargs,
        )


class SSEHandler:
    """Handler for Server-Sent Events streaming."""

    def __init__(self, event_emitter: StreamingEventEmitter, request: Request):
        self.event_emitter = event_emitter
        self.request = request
        self.logger = logger.bind(
            request_id=event_emitter.request_id,
            correlation_id=event_emitter.correlation_id,
        )
        self._connected = False
        self._heartbeat_interval = 30  # seconds

    async def stream_events(self) -> AsyncGenerator[str, None]:
        """Generate SSE formatted events."""
        self._connected = True
        self.logger.info("SSE stream started")

        try:
            # Send initial connection event
            yield self._format_sse_event(
                "connected",
                {
                    "message": "Stream connected",
                    "request_id": self.event_emitter.request_id,
                    "correlation_id": self.event_emitter.correlation_id,
                },
            )

            # Send any buffered events first
            buffered_events = self.event_emitter.get_events()
            for event in buffered_events:
                if await self._is_client_connected():
                    yield self._format_sse_event("event", self._event_to_payload(event))
                else:
                    break

            # Stream new events
            heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            try:
                while self._connected and await self._is_client_connected():
                    try:
                        # Wait for next event with timeout
                        event = await self.event_emitter.get_next_event(timeout=1.0)

                        if event:
                            # Send the domain event
                            yield self._format_sse_event(
                                "event", self._event_to_payload(event)
                            )
                            # If we see terminal workflow events, we can end the stream
                            try:
                                if getattr(event, "event_type", None) in (
                                    EventType.WORKFLOW_COMPLETED,
                                    EventType.WORKFLOW_FAILED,
                                ):
                                    break
                            except Exception:
                                # Be resilient if enum import/types change
                                pass
                        else:
                            # If streaming has been disabled and no more events, end the stream
                            if (
                                hasattr(self.event_emitter, "is_streaming_enabled")
                                and not self.event_emitter.is_streaming_enabled()
                            ):
                                break

                    except asyncio.TimeoutError:
                        # Continue loop on timeout
                        continue
                    except Exception as e:
                        self.logger.error("Error streaming event", error=str(e))
                        yield self._format_sse_event(
                            "error",
                            {"message": "Stream error occurred", "error": str(e)},
                        )
                        break

            finally:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

        except Exception as e:
            self.logger.error("SSE stream error", error=str(e))
            yield self._format_sse_event(
                "error", {"message": "Stream terminated due to error", "error": str(e)}
            )

        finally:
            self._connected = False
            self.logger.info("SSE stream ended")
            yield self._format_sse_event(
                "disconnected", {"message": "Stream disconnected"}
            )

    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeat messages."""
        while self._connected:
            try:
                await asyncio.sleep(self._heartbeat_interval)
                if self._connected:
                    # Heartbeat will be sent by the main loop
                    pass
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error("Heartbeat error", error=str(e))
                break

    async def _is_client_connected(self) -> bool:
        """Check if the client is still connected."""
        try:
            # Check if the request is still active
            if hasattr(self.request, "is_disconnected"):
                return not await self.request.is_disconnected()
            return True
        except Exception:
            return False

    def _event_to_payload(self, event: StreamingEvent) -> dict:
        """Convert StreamingEvent to a serializable dict (Pydantic v1/v2 compatible)."""
        try:
            # Pydantic v2
            return event.model_dump()  # type: ignore[attr-defined]
        except Exception:
            # Fallback (Pydantic v1)
            return event.dict()

    def _format_sse_event(
        self, event_type: str, data: dict, event_id: Optional[str] = None
    ) -> str:
        """Format data as SSE event."""
        lines = []

        if event_id:
            lines.append(f"id: {event_id}")

        lines.append(f"event: {event_type}")

        # Format data as JSON
        json_data = json.dumps(data, default=str, ensure_ascii=False)
        for line in json_data.split("\n"):
            lines.append(f"data: {line}")

        lines.append("")  # Empty line to end the event
        return "\n".join(lines) + "\n"

    def disconnect(self) -> None:
        """Disconnect the SSE stream."""
        self._connected = False
        self.logger.info("SSE stream disconnected by handler")


def create_sse_response(
    event_emitter: StreamingEventEmitter, request: Request
) -> SSEResponse:
    """Create an SSE response for streaming events."""
    handler = SSEHandler(event_emitter, request)

    # Add cleanup task
    def cleanup():
        handler.disconnect()
        event_emitter.remove_stream(handler)
        cleanup_event_emitter(
            event_emitter.request_id,
            event_emitter.correlation_id,
        )

    # Add the handler as a stream
    event_emitter.add_stream(handler)

    return SSEResponse(handler.stream_events(), background=BackgroundTask(cleanup))


class SSEEventSender:
    """Helper class for sending events to SSE streams."""

    def __init__(self, handler: SSEHandler):
        self.handler = handler
        self._queue: asyncio.Queue = asyncio.Queue()

    def send_event(self, event: StreamingEvent) -> None:
        """Send an event to the SSE stream."""
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("SSE event queue is full, dropping event")

    async def get_next_event(
        self, timeout: Optional[float] = None
    ) -> Optional[StreamingEvent]:
        """Get the next event from the queue."""
        try:
            if timeout:
                return await asyncio.wait_for(self._queue.get(), timeout=timeout)
            else:
                return await self._queue.get()
        except asyncio.TimeoutError:
            return None
