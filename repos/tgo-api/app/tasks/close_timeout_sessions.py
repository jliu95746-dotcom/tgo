"""Periodic task to close timed-out sessions."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict
from uuid import UUID

from sqlalchemy import and_
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.models import VisitorSession, SessionStatus, VisitorAssignmentRule
from app.services.session_service import close_visitor_session

logger = get_logger("tasks.close_timeout_sessions")

# Global state
_task: Optional[asyncio.Task] = None
_processing_lock = asyncio.Lock()


def _get_project_timeout_hours(
    db: Session, project_id: UUID, cache: Dict[UUID, int]
) -> int:
    (
        '\n'
        '    Get session timeout hours for a project.\n'
        '    \n'
        '    Uses VisitorAssignmentRule.auto_close_hours if configured,\n'
        '    otherwise falls back to settings.SESSION_DEFAULT_TIMEOUT_HOURS.\n'
        '    \n'
        '    Results are cached to avoid repeated database queries.\n'
        '    '
    )
    if project_id in cache:
        return cache[project_id]

    rule = (
        db.query(VisitorAssignmentRule)
        .filter(VisitorAssignmentRule.project_id == project_id)
        .first()
    )

    if rule and rule.auto_close_hours:
        timeout_hours = rule.auto_close_hours
    else:
        timeout_hours = settings.SESSION_DEFAULT_TIMEOUT_HOURS

    cache[project_id] = timeout_hours
    return timeout_hours


async def _process_timeout_sessions() -> int:
    (
        '\n'
        '    Process and close timed-out sessions.\n'
        '    \n'
        '    Returns:\n'
        '        Number of sessions closed\n'
        '    '
    )
    db: Session = SessionLocal()
    closed_count = 0
    timeout_cache: Dict[UUID, int] = {}  # project_id -> timeout_hours

    try:
        # Get the minimum timeout to find potential candidates
        # (we'll check per-project timeout later)
        min_timeout_hours = settings.SESSION_DEFAULT_TIMEOUT_HOURS
        cutoff_time = datetime.utcnow() - timedelta(hours=min_timeout_hours)

        # Query open sessions that might be timed out
        # We check last_message_at or updated_at (whichever is later)
        # to determine if session is inactive
        sessions = (
            db.query(VisitorSession)
            .filter(
                VisitorSession.status == SessionStatus.OPEN.value,
                # Session hasn't been updated recently (based on last activity)
                VisitorSession.updated_at < cutoff_time,
            )
            .options(joinedload(VisitorSession.visitor))
            .limit(settings.SESSION_TIMEOUT_BATCH_SIZE)
            .all()
        )

        if not sessions:
            logger.debug("No potentially timed-out sessions found")
            return 0

        logger.info(
            f"Found {len(sessions)} potentially timed-out sessions to check"
        )

        for session in sessions:
            try:
                # Get project-specific timeout
                timeout_hours = _get_project_timeout_hours(
                    db, session.project_id, timeout_cache
                )

                # Calculate the actual cutoff time for this project
                project_cutoff = datetime.utcnow() - timedelta(
                    hours=timeout_hours
                )

                # Determine last activity time
                last_activity = session.last_message_at or session.updated_at

                # Check if session is actually timed out
                if last_activity >= project_cutoff:
                    logger.debug(
                        (
                            f'Session '
                            f'{session.id}'
                            f' not yet timed out (last_activity='
                            f'{last_activity}'
                            f', cutoff='
                            f'{project_cutoff}'
                            f')'
                        )
                    )
                    continue

                # Close the session
                logger.info(
                    f"Closing timed-out session {session.id}",
                    extra={
                        "session_id": str(session.id),
                        "visitor_id": str(session.visitor_id),
                        "project_id": str(session.project_id),
                        "last_activity": str(last_activity),
                        "timeout_hours": timeout_hours,
                    },
                )

                await close_visitor_session(
                    db=db,
                    session=session,
                    closed_by_staff=None,  # System closure
                    send_notification=True,
                    auto_commit=True,
                    reason=f"timeout ({timeout_hours}h inactivity)",
                )

                closed_count += 1

            except ValueError as e:
                # Session already closed
                logger.debug(f"Session {session.id} already closed: {e}")
            except Exception as e:
                logger.error(
                    f"Failed to close timed-out session {session.id}: {e}",
                    extra={"session_id": str(session.id), "error": str(e)},
                )
                # Continue with other sessions

        logger.info(f"Closed {closed_count} timed-out sessions")
        return closed_count

    except Exception as e:
        logger.error(f"Error processing timeout sessions: {e}")
        return closed_count
    finally:
        db.close()


async def _run_periodic_task():
    """Run the periodic session timeout check."""
    logger.info(
        f"Starting session timeout check task "
        f"(interval={settings.SESSION_TIMEOUT_CHECK_INTERVAL_SECONDS}s, "
        f"default_timeout={settings.SESSION_DEFAULT_TIMEOUT_HOURS}h)"
    )

    while True:
        try:
            async with _processing_lock:
                closed_count = await _process_timeout_sessions()
                if closed_count > 0:
                    logger.info(
                        (
                            f'Periodic check: closed '
                            f'{closed_count}'
                            f' timed-out sessions'
                        )
                    )
        except Exception as e:
            logger.error(f"Error in periodic session timeout check: {e}")

        await asyncio.sleep(settings.SESSION_TIMEOUT_CHECK_INTERVAL_SECONDS)


async def start_session_timeout_task():
    """Start the background session timeout check task."""
    global _task

    if not settings.SESSION_TIMEOUT_CHECK_ENABLED:
        logger.info("Session timeout check is disabled")
        return

    if _task is not None and not _task.done():
        logger.warning("Session timeout check task is already running")
        return

    _task = asyncio.create_task(_run_periodic_task())
    logger.info("Session timeout check task started")


async def stop_session_timeout_task():
    """Stop the background session timeout check task."""
    global _task

    if _task is None:
        return

    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass

    _task = None
    logger.info("Session timeout check task stopped")


async def trigger_timeout_check() -> int:
    (
        '\n'
        '    Manually trigger a session timeout check.\n'
        '    \n'
        '    Can be called from an API endpoint for immediate processing.\n'
        '    \n'
        '    Returns:\n'
        '        Number of sessions closed\n'
        '    '
    )
    async with _processing_lock:
        return await _process_timeout_sessions()
