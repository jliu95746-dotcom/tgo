"""Waiting queue management tasks.

This module provides:
1. Fallback processing - Low-frequency periodic processing for missed entries
2. Expired entries cleanup - Periodic cleanup of expired queue entries
3. Legacy trigger function for backward compatibility
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Optional, Set
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.models import (
    VisitorWaitingQueue,
    WaitingStatus,
    AssignmentSource,
)
from app.services.transfer_service import transfer_to_staff
from app.services.queue_timeout_service import process_queue_timeouts

logger = get_logger("tasks.process_waiting_queue")

# Global state for tasks
_fallback_task: Optional[asyncio.Task] = None
_cleanup_task: Optional[asyncio.Task] = None
_processing_lock = asyncio.Lock()
_processing_ids: Set[UUID] = set()
_semaphore: Optional[asyncio.Semaphore] = None


def _get_semaphore() -> asyncio.Semaphore:
    """Get or create the semaphore for concurrent processing control."""
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(settings.QUEUE_PROCESS_MAX_WORKERS)
    return _semaphore


# =============================================================================
# Fallback Processing - Low-frequency periodic processing for missed entries
# =============================================================================

async def _process_fallback_batch() -> None:
    """
    Fallback batch processing for queue entries that may have been missed.
    
    This runs at low frequency (default 2 minutes) to catch:
    - Entries missed due to event trigger failures
    - Entries present after system restart
    - Entries where staff became available without triggering events
    
    Only processes entries that:
    - Are in WAITING status
    - Are not expired (expired_at > now or expired_at is null)
    - Haven't been attempted recently (last_attempt_at < now - fallback_interval)
    """
    db = SessionLocal()
    try:
        fallback_delay = timedelta(seconds=settings.QUEUE_FALLBACK_INTERVAL_SECONDS)
        cutoff_time = datetime.utcnow() - fallback_delay
        
        # Query entries that need fallback processing
        entries = (
            db.query(VisitorWaitingQueue)
            .filter(
                VisitorWaitingQueue.status == WaitingStatus.WAITING.value,
                # Not expired
                (
                    (VisitorWaitingQueue.expired_at.is_(None)) |
                    (VisitorWaitingQueue.expired_at > func.now())
                ),
                # Haven't been attempted recently
                (
                    (VisitorWaitingQueue.last_attempt_at.is_(None)) |
                    (VisitorWaitingQueue.last_attempt_at < cutoff_time)
                ),
            )
            .order_by(
                VisitorWaitingQueue.priority.desc(),
                VisitorWaitingQueue.position.asc(),
            )
            .limit(settings.QUEUE_PROCESS_BATCH_SIZE)
            .all()
        )

        if not entries:
            logger.debug("Fallback processor: no entries to process")
            return

        # Filter out entries already being processed
        async with _processing_lock:
            entries_to_process = [e for e in entries if e.id not in _processing_ids]
            for e in entries_to_process:
                _processing_ids.add(e.id)

        if not entries_to_process:
            logger.debug("Fallback processor: all entries already being processed")
            return

        logger.info(
            f"Fallback processor: processing {len(entries_to_process)} entries",
            extra={"count": len(entries_to_process)},
        )

        # Group entries by project for efficient processing
        project_entries: dict[UUID, list[VisitorWaitingQueue]] = {}
        for entry in entries_to_process:
            if entry.project_id not in project_entries:
                project_entries[entry.project_id] = []
            project_entries[entry.project_id].append(entry)

        # Process each project's entries
        semaphore = _get_semaphore()
        
        async def process_project_entries(
            project_id: UUID,
            entries: list[VisitorWaitingQueue]
        ) -> tuple[int, int]:
            """Process entries for a single project."""
            async with semaphore:
                entry_db = SessionLocal()
                assigned = 0
                processed = 0
                try:
                    for entry in entries:
                        try:
                            # Re-fetch entry to get latest state
                            fresh_entry = (
                                entry_db.query(VisitorWaitingQueue)
                                .filter(
                                    VisitorWaitingQueue.id == entry.id,
                                    VisitorWaitingQueue.status == WaitingStatus.WAITING.value,
                                )
                                .first()
                            )
                            
                            if not fresh_entry:
                                continue
                                
                            processed += 1
                            
                            result = await transfer_to_staff(
                                db=entry_db,
                                visitor_id=fresh_entry.visitor_id,
                                project_id=project_id,
                                source=AssignmentSource.RULE,
                                visitor_message=fresh_entry.visitor_message,
                                session_id=fresh_entry.session_id,
                                notes=f"Fallback processing (entry_id={fresh_entry.id})",
                                skip_queue_status_check=True,
                                auto_commit=False,
                                add_to_queue_if_no_staff=False,  # Already in queue
                                expected_queue_entry_id=fresh_entry.id,
                            )
                            
                            if result.success and result.assigned_staff_id:
                                entry_db.commit()
                                assigned += 1
                                logger.info(
                                    f"Fallback: entry {fresh_entry.id} assigned to {result.assigned_staff_id}",
                                    extra={
                                        "entry_id": str(fresh_entry.id),
                                        "staff_id": str(result.assigned_staff_id),
                                    }
                                )
                            else:
                                entry_db.commit()  # Commit attempt record
                                # No staff available, stop processing this project
                                break
                                
                        except Exception as e:
                            logger.error(f"Fallback: error processing entry {entry.id}: {e}")
                            try:
                                entry_db.rollback()
                            except Exception:
                                pass
                                
                    return assigned, processed
                finally:
                    entry_db.close()

        # Run project processing in parallel
        tasks = [
            process_project_entries(pid, pentries)
            for pid, pentries in project_entries.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Summarize results
        total_assigned = 0
        total_processed = 0
        for result in results:
            if isinstance(result, tuple):
                total_assigned += result[0]
                total_processed += result[1]

        # Remove from processing set
        async with _processing_lock:
            for e in entries_to_process:
                _processing_ids.discard(e.id)

        logger.info(
            f"Fallback processor: batch complete",
            extra={
                "total": len(entries_to_process),
                "processed": total_processed,
                "assigned": total_assigned,
            },
        )

    except Exception as e:
        logger.exception(f"Fallback processor: batch exception: {e}")
    finally:
        db.close()


async def _fallback_loop() -> None:
    """Fallback processing loop."""
    interval_sec = max(1, settings.QUEUE_FALLBACK_INTERVAL_SECONDS)
    while True:
        try:
            await _process_fallback_batch()
        except Exception as e:
            logger.exception(f"Fallback processor loop exception: {e}")
        await asyncio.sleep(interval_sec)


# =============================================================================
# Expired Entries Cleanup
# =============================================================================

async def _cleanup_expired_entries() -> None:
    """Run committed queue transitions and recover pending timeout notices."""
    db = SessionLocal()
    try:
        count = await process_queue_timeouts(db)
        if count:
            logger.info("Expired waiting queue entries", extra={"count": count})
    except Exception as exc:
        db.rollback()
        logger.warning("Queue cleanup failed", extra={"error_type": type(exc).__name__})
    finally:
        db.close()


async def _cleanup_loop() -> None:
    """Cleanup task loop."""
    interval_sec = max(1, settings.QUEUE_CLEANUP_INTERVAL_SECONDS)
    while True:
        try:
            await _cleanup_expired_entries()
        except Exception as e:
            logger.exception(f"Cleanup loop exception: {e}")
        await asyncio.sleep(interval_sec)


# =============================================================================
# Task Lifecycle Management
# =============================================================================

def start_queue_tasks() -> None:
    """Start queue processing background tasks."""
    global _fallback_task, _cleanup_task
    
    # Start fallback processor if enabled
    if settings.QUEUE_FALLBACK_ENABLED:
        if _fallback_task is None or _fallback_task.done():
            try:
                _fallback_task = asyncio.create_task(_fallback_loop())
                logger.info(
                    "Queue fallback processor started",
                    extra={"interval_seconds": settings.QUEUE_FALLBACK_INTERVAL_SECONDS},
                )
            except Exception as e:
                logger.warning(f"Failed to start fallback processor: {e}")
    else:
        logger.info("Queue fallback processor disabled by config")
    
    # Start cleanup task
    if _cleanup_task is None or _cleanup_task.done():
        try:
            _cleanup_task = asyncio.create_task(_cleanup_loop())
            logger.info(
                "Queue cleanup task started",
                extra={"interval_seconds": settings.QUEUE_CLEANUP_INTERVAL_SECONDS},
            )
        except Exception as e:
            logger.warning(f"Failed to start cleanup task: {e}")


async def stop_queue_tasks() -> None:
    """Stop queue processing background tasks."""
    global _fallback_task, _cleanup_task
    
    tasks_to_stop = []
    
    if _fallback_task:
        _fallback_task.cancel()
        tasks_to_stop.append(_fallback_task)
        
    if _cleanup_task:
        _cleanup_task.cancel()
        tasks_to_stop.append(_cleanup_task)
    
    for task in tasks_to_stop:
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
    
    _fallback_task = None
    _cleanup_task = None
    logger.info("Queue tasks stopped")


# =============================================================================
# Legacy Functions (for backward compatibility)
# =============================================================================

async def trigger_process_entry(entry_id: UUID) -> None:
    """
    Trigger processing of a queue entry.
    
    This is a backward-compatible wrapper that delegates to the
    new queue_trigger_service.
    
    Args:
        entry_id: UUID of the VisitorWaitingQueue entry to process
    """
    from app.services.queue_trigger_service import trigger_queue_for_entry
    await trigger_queue_for_entry(entry_id)


# Legacy aliases for backward compatibility
start_queue_processor = start_queue_tasks
stop_queue_processor = stop_queue_tasks
