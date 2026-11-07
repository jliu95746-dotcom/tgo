"""
Event emitter for streaming coordination workflow events.
"""

import asyncio
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from pydantic import JsonValue

from app.core.logging import get_logger
from app.models.streaming import (
    StreamingEvent,
    EventType,
    EventSeverity,
    BaseEventData,
)


logger = get_logger(__name__)


class EventEmitter:
    """Base event emitter for coordination workflow events."""

    def __init__(self, request_id: str, correlation_id: str):
        self.request_id = request_id
        self.correlation_id = correlation_id
        self.listeners: Dict[EventType, List[Callable]] = {}
        self._event_buffer: List[StreamingEvent] = []
        self.logger = logger.bind(
            request_id=request_id, correlation_id=correlation_id
        )

    def on(
        self, event_type: EventType, callback: Callable[[StreamingEvent], None]
    ) -> None:
        """Register an event listener."""
        if event_type not in self.listeners:
            self.listeners[event_type] = []
        self.listeners[event_type].append(callback)

    def off(
        self, event_type: EventType, callback: Callable[[StreamingEvent], None]
    ) -> None:
        """Remove an event listener."""
        if event_type in self.listeners:
            try:
                self.listeners[event_type].remove(callback)
            except ValueError:
                pass

    def emit(
        self,
        event_type: EventType,
        data: BaseEventData,
        severity: EventSeverity = EventSeverity.INFO,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> StreamingEvent:
        """Emit an event to all listeners."""
        event = StreamingEvent(
            event_type=event_type,
            timestamp=datetime.utcnow(),
            correlation_id=self.correlation_id,
            request_id=self.request_id,
            severity=severity,
            data=data,
            metadata=metadata or {},
        )

        # Add to buffer

        self._event_buffer.append(event)

        # Notify listeners

        if event_type in self.listeners:
            for callback in self.listeners[event_type]:
                try:
                    callback(event)
                except Exception as e:
                    self.logger.error(
                        "Error in event listener",
                        event_type=event_type,
                        error=str(e),
                    )
        return event

    def get_events(self) -> List[StreamingEvent]:
        """Get all events from the buffer."""
        return self._event_buffer.copy()

    def clear_events(self) -> None:
        """Clear the event buffer."""
        self._event_buffer.clear()


class StreamingEventEmitter(EventEmitter):
    """Event emitter with streaming capabilities."""

    def __init__(self, request_id: str, correlation_id: str):
        super().__init__(request_id, correlation_id)
        self._streaming_enabled = False
        self._stream_queue: Optional[asyncio.Queue[StreamingEvent]] = None
        self._active_streams: List[Any] = []

    def enable_streaming(self) -> None:
        """Enable streaming mode."""
        self._streaming_enabled = True
        self._stream_queue = asyncio.Queue()

    def disable_streaming(self) -> None:
        """Disable streaming mode."""
        self._streaming_enabled = False
        self._stream_queue = None

    def is_streaming_enabled(self) -> bool:
        """Check if streaming is enabled."""
        return self._streaming_enabled

    def add_stream(self, stream: Any) -> None:
        """Add a stream to receive events."""
        if stream not in self._active_streams:
            self._active_streams.append(stream)

    def remove_stream(self, stream: Any) -> None:
        """Remove a stream from receiving events."""
        if stream in self._active_streams:
            self._active_streams.remove(stream)

    def emit(
        self,
        event_type: EventType,
        data: BaseEventData,
        severity: EventSeverity = EventSeverity.INFO,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> StreamingEvent:
        """Emit an event and send to streams if enabled."""
        event = super().emit(event_type, data, severity, metadata)

        # Send to streams if streaming is enabled

        if self._streaming_enabled:
            self._send_to_streams(event)
        return event

    def _send_to_streams(self, event: StreamingEvent) -> None:
        """Send event to all active streams."""
        if self._stream_queue:
            try:
                self._stream_queue.put_nowait(event)
            except asyncio.QueueFull:
                self.logger.warning("Stream queue is full, dropping event")
        # Send to active streams

        for stream in self._active_streams[
            :
        ]:  # Copy to avoid modification during iteration
            try:
                if hasattr(stream, 'send_event'):
                    stream.send_event(event)
                elif hasattr(stream, 'put_nowait'):
                    stream.put_nowait(event)
            except Exception as e:
                self.logger.error(
                    "Failed to send event to stream", error=str(e)
                )
                # Remove failed stream

                self._active_streams.remove(stream)

    async def get_next_event(
        self, timeout: Optional[float] = None
    ) -> Optional[StreamingEvent]:
        """Get the next event from the stream queue."""
        if not self._stream_queue:
            return None
        try:
            if timeout:
                return await asyncio.wait_for(
                    self._stream_queue.get(), timeout=timeout
                )
            else:
                return await self._stream_queue.get()
        except asyncio.TimeoutError:
            return None

    def get_stream_stats(self) -> dict[str, JsonValue]:
        """Get streaming statistics."""
        return {
            "streaming_enabled": self._streaming_enabled,
            "active_streams": len(self._active_streams),
            "queue_size": self._stream_queue.qsize()
            if self._stream_queue
            else 0,
            "total_events": len(self._event_buffer),
        }


# Global event emitter registry

_event_emitters: Dict[str, StreamingEventEmitter] = {}


def get_event_emitter(
    request_id: str, correlation_id: str
) -> StreamingEventEmitter:
    """Get or create an event emitter for a request."""
    key = f"{request_id}:{correlation_id}"

    if key not in _event_emitters:
        _event_emitters[key] = StreamingEventEmitter(
            request_id, correlation_id
        )
    return _event_emitters[key]


def cleanup_event_emitter(request_id: str, correlation_id: str) -> None:
    """Clean up an event emitter."""
    key = f"{request_id}:{correlation_id}"

    if key in _event_emitters:
        emitter = _event_emitters[key]
        emitter.disable_streaming()
        emitter.clear_events()
        del _event_emitters[key]


def get_active_emitters() -> Dict[str, StreamingEventEmitter]:
    """Get all active event emitters."""
    return _event_emitters.copy()
