"""
Stream manager for coordinating streaming sessions and event distribution.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set
from uuid import uuid4
from pydantic import JsonValue

from app.core.logging import get_logger
from .event_emitter import (
    get_event_emitter,
    cleanup_event_emitter,
)


logger = get_logger(__name__)


class StreamingSession:
    """Represents an active streaming session."""

    def __init__(self, session_id: str, request_id: str, correlation_id: str):
        self.session_id = session_id
        self.request_id = request_id
        self.correlation_id = correlation_id
        self.created_at = datetime.utcnow()
        self.last_activity = datetime.utcnow()
        self.event_emitter = get_event_emitter(request_id, correlation_id)
        self.is_active = True
        self._subscribers: Set[str] = set()

    def add_subscriber(self, subscriber_id: str) -> None:
        """Add a subscriber to this session."""
        self._subscribers.add(subscriber_id)
        self.last_activity = datetime.utcnow()

    def remove_subscriber(self, subscriber_id: str) -> None:
        """Remove a subscriber from this session."""
        self._subscribers.discard(subscriber_id)
        self.last_activity = datetime.utcnow()

    def has_subscribers(self) -> bool:
        """Check if the session has active subscribers."""
        return len(self._subscribers) > 0

    def get_subscriber_count(self) -> int:
        """Get the number of active subscribers."""
        return len(self._subscribers)

    def is_expired(self, timeout_minutes: int = 60) -> bool:
        """Check if the session has expired."""
        expiry_time = self.created_at + timedelta(minutes=timeout_minutes)
        return datetime.utcnow() > expiry_time

    def is_inactive(self, inactive_minutes: int = 10) -> bool:
        """Check if the session has been inactive."""
        inactive_time = self.last_activity + timedelta(
            minutes=inactive_minutes
        )
        return datetime.utcnow() > inactive_time

    def close(self) -> None:
        """Close the streaming session."""
        self.is_active = False
        cleanup_event_emitter(self.request_id, self.correlation_id)

    def get_stats(self) -> dict[str, JsonValue]:
        """Get session statistics."""
        return {
            "session_id": self.session_id,
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            "is_active": self.is_active,
            "subscriber_count": self.get_subscriber_count(),
            "event_stats": self.event_emitter.get_stream_stats()
            if self.event_emitter
            else {},
        }


class StreamManager:
    """Manages streaming sessions and event distribution."""

    def __init__(self) -> None:
        self.sessions: Dict[str, StreamingSession] = {}
        self.request_to_session: Dict[str, str] = {}
        self._cleanup_task: Optional[asyncio.Task] = None
        self._cleanup_interval = 300  # 5 minutes
        self.logger = logger.bind(component="stream_manager")

    def start(self) -> None:
        """Start the stream manager."""
        if not self._cleanup_task or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        self.logger.info("Stream manager started")

    def stop(self) -> None:
        """Stop the stream manager."""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
        # Close all sessions

        for session in list(self.sessions.values()):
            session.close()
        self.sessions.clear()
        self.request_to_session.clear()
        self.logger.info("Stream manager stopped")

    def create_session(
        self, request_id: str, correlation_id: str
    ) -> StreamingSession:
        """Create a new streaming session."""
        session_id = str(uuid4())
        session = StreamingSession(session_id, request_id, correlation_id)

        self.sessions[session_id] = session
        self.request_to_session[request_id] = session_id

        # Enable streaming on the event emitter

        session.event_emitter.enable_streaming()

        self.logger.info(
            "Created streaming session",
            session_id=session_id,
            request_id=request_id,
            correlation_id=correlation_id,
        )

        return session

    def get_session(self, session_id: str) -> Optional[StreamingSession]:
        """Get a streaming session by ID."""
        return self.sessions.get(session_id)

    def get_session_by_request(
        self, request_id: str
    ) -> Optional[StreamingSession]:
        """Get a streaming session by request ID."""
        session_id = self.request_to_session.get(request_id)
        return self.sessions.get(session_id) if session_id else None

    def close_session(self, session_id: str) -> bool:
        """Close a streaming session."""
        session = self.sessions.get(session_id)
        if not session:
            return False
        session.close()

        # Remove from tracking

        self.sessions.pop(session_id, None)
        if session.request_id in self.request_to_session:
            self.request_to_session.pop(session.request_id, None)
        self.logger.info(
            "Closed streaming session",
            session_id=session_id,
            request_id=session.request_id,
        )

        return True

    def add_subscriber(self, session_id: str, subscriber_id: str) -> bool:
        """Add a subscriber to a session."""
        session = self.sessions.get(session_id)
        if not session:
            return False
        session.add_subscriber(subscriber_id)
        self.logger.debug(
            "Added subscriber to session",
            session_id=session_id,
            subscriber_id=subscriber_id,
            total_subscribers=session.get_subscriber_count(),
        )

        return True

    def remove_subscriber(self, session_id: str, subscriber_id: str) -> bool:
        """Remove a subscriber from a session."""
        session = self.sessions.get(session_id)
        if not session:
            return False
        session.remove_subscriber(subscriber_id)
        self.logger.debug(
            "Removed subscriber from session",
            session_id=session_id,
            subscriber_id=subscriber_id,
            total_subscribers=session.get_subscriber_count(),
        )

        return True

    def get_active_sessions(self) -> List[StreamingSession]:
        """Get all active streaming sessions."""
        return [
            session for session in self.sessions.values() if session.is_active
        ]

    def get_session_stats(self) -> dict[str, JsonValue]:
        """Get statistics for all sessions."""
        active_sessions = self.get_active_sessions()

        return {
            "total_sessions": len(self.sessions),
            "active_sessions": len(active_sessions),
            "total_subscribers": sum(
                s.get_subscriber_count() for s in active_sessions
            ),
            "sessions": [session.get_stats() for session in active_sessions],
        }

    async def _cleanup_loop(self) -> None:
        """Periodic cleanup of expired and inactive sessions."""
        while True:
            try:
                await asyncio.sleep(self._cleanup_interval)
                await self._cleanup_sessions()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error("Error in cleanup loop", error=str(e))

    async def _cleanup_sessions(self) -> None:
        """Clean up expired and inactive sessions."""
        sessions_to_remove = []

        for session_id, session in self.sessions.items():
            should_remove = False

            # Check if session is expired

            if session.is_expired():
                self.logger.info(
                    "Session expired",
                    session_id=session_id,
                    created_at=session.created_at,
                )
                should_remove = True
            # Check if session is inactive and has no subscribers

            elif session.is_inactive() and not session.has_subscribers():
                self.logger.info(
                    "Session inactive with no subscribers",
                    session_id=session_id,
                    last_activity=session.last_activity,
                )
                should_remove = True
            if should_remove:
                sessions_to_remove.append(session_id)
        # Remove expired/inactive sessions

        for session_id in sessions_to_remove:
            self.close_session(session_id)
        if sessions_to_remove:
            self.logger.info(
                "Cleaned up sessions",
                removed_count=len(sessions_to_remove),
                remaining_count=len(self.sessions),
            )


# Global stream manager instance

_stream_manager: Optional[StreamManager] = None


def get_stream_manager() -> StreamManager:
    """Get the global stream manager instance."""
    global _stream_manager

    if _stream_manager is None:
        _stream_manager = StreamManager()
        _stream_manager.start()
    return _stream_manager


def shutdown_stream_manager() -> None:
    """Shutdown the global stream manager."""
    global _stream_manager

    if _stream_manager:
        _stream_manager.stop()
        _stream_manager = None
