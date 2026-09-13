"""Queue trigger service for event-driven queue processing.

This service provides methods to trigger queue processing when:
- Staff becomes available (resumes service, goes online, finishes a session)
- Visitor enters the queue (immediate attempt)
"""

from __future__ import annotations

import asyncio
from typing import List, Optional, Set
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.models import (
    Staff,
    StaffRole,
    VisitorAssignmentRule,
    VisitorSession,
    SessionStatus,
    VisitorWaitingQueue,
    WaitingStatus,
)

logger = get_logger("services.queue_trigger")

# Global state for tracking in-progress processing
_processing_lock = asyncio.Lock()
_processing_project_ids: Set[UUID] = set()
_semaphore: Optional[asyncio.Semaphore] = None


def _get_semaphore() -> asyncio.Semaphore:
    """Get or create the semaphore for concurrent processing control."""
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(settings.QUEUE_PROCESS_MAX_WORKERS)
    return _semaphore


async def trigger_queue_for_project(project_id: UUID) -> None:
    """
    Trigger queue processing for a specific project.
    
    This should be called when staff becomes available in a project:
    - Staff resumes service (service_paused = False)
    - Staff activates service (is_active = True)
    - Staff session closes (frees up a slot)
    
    Args:
        project_id: The project ID to process queue for
    """
    # Check if already processing this project
    async with _processing_lock:
        if project_id in _processing_project_ids:
            logger.debug(f"Project {project_id} queue is already being processed")
            return
        _processing_project_ids.add(project_id)
    
    try:
        # Process in background
        asyncio.create_task(_process_project_queue_internal(project_id))
    except Exception as e:
        logger.error(f"Failed to trigger queue processing for project {project_id}: {e}")
        async with _processing_lock:
            _processing_project_ids.discard(project_id)


async def trigger_queue_for_staff(staff_id: UUID, project_id: UUID) -> None:
    """
    Trigger queue processing when a specific staff becomes available.
    
    This is a convenience wrapper that triggers project-level processing.
    
    Args:
        staff_id: The staff ID that became available
        project_id: The project ID the staff belongs to
    """
    logger.info(
        f"Staff {staff_id} triggered queue processing for project {project_id}",
        extra={"staff_id": str(staff_id), "project_id": str(project_id)}
    )
    await trigger_queue_for_project(project_id)


async def _process_project_queue_internal(project_id: UUID) -> None:
    """
    Internal method to process waiting queue for a project.
    
    Attempts to assign waiting visitors to available staff.
    """
    # Import here to avoid circular imports
    from app.services.transfer_service import transfer_to_staff
    from app.models import AssignmentSource
    
    semaphore = _get_semaphore()
    
    try:
        async with semaphore:
            db = SessionLocal()
            try:
                # Get waiting queue entries for this project
                waiting_entries = (
                    db.query(VisitorWaitingQueue)
                    .filter(
                        VisitorWaitingQueue.project_id == project_id,
                        VisitorWaitingQueue.status == WaitingStatus.WAITING.value,
                        # Only process non-expired entries
                        (
                            (VisitorWaitingQueue.expired_at.is_(None)) |
                            (VisitorWaitingQueue.expired_at > func.now())
                        ),
                    )
                    .order_by(
                        VisitorWaitingQueue.priority.desc(),
                        VisitorWaitingQueue.position.asc(),
                    )
                    .limit(settings.QUEUE_PROCESS_BATCH_SIZE)
                    .all()
                )
                
                if not waiting_entries:
                    logger.debug(f"No waiting entries for project {project_id}")
                    return
                
                logger.info(
                    f"Processing {len(waiting_entries)} waiting entries for project {project_id}",
                    extra={"project_id": str(project_id), "count": len(waiting_entries)}
                )
                
                # Process each entry
                assigned_count = 0
                for entry in waiting_entries:
                    try:
                        result = await transfer_to_staff(
                            db=db,
                            visitor_id=entry.visitor_id,
                            project_id=project_id,
                            source=AssignmentSource.RULE,
                            visitor_message=entry.visitor_message,
                            session_id=entry.session_id,
                            notes=f"From queue trigger (entry_id={entry.id})",
                            skip_queue_status_check=True,
                            auto_commit=False,
                            ai_disabled=entry.ai_disabled,
                            add_to_queue_if_no_staff=False,  # Already in queue
                            expected_queue_entry_id=entry.id,
                        )
                        
                        if result.success and result.assigned_staff_id:
                            db.commit()
                            assigned_count += 1
                            logger.info(
                                f"Queue entry {entry.id} assigned to staff {result.assigned_staff_id}",
                                extra={
                                    "entry_id": str(entry.id),
                                    "visitor_id": str(entry.visitor_id),
                                    "staff_id": str(result.assigned_staff_id),
                                }
                            )
                        else:
                            # No staff available, stop processing
                            # (next entries won't find staff either)
                            db.commit()  # Commit the attempt record
                            logger.debug(
                                f"No staff available for entry {entry.id}, stopping batch",
                                extra={"entry_id": str(entry.id)}
                            )
                            break
                            
                    except Exception as e:
                        logger.error(
                            f"Error processing queue entry {entry.id}: {e}",
                            extra={"entry_id": str(entry.id)}
                        )
                        try:
                            db.rollback()
                        except Exception:
                            pass
                
                logger.info(
                    f"Queue processing complete for project {project_id}",
                    extra={
                        "project_id": str(project_id),
                        "assigned_count": assigned_count,
                        "total_processed": len(waiting_entries),
                    }
                )
                
            finally:
                db.close()
                
    except Exception as e:
        logger.exception(f"Error in queue processing for project {project_id}: {e}")
    finally:
        # Remove from processing set
        async with _processing_lock:
            _processing_project_ids.discard(project_id)


async def trigger_queue_for_entry(entry_id: UUID) -> bool:
    """
    Trigger immediate processing for a specific queue entry.
    
    This should be called when a visitor enters the queue to
    attempt immediate assignment.
    
    Args:
        entry_id: The queue entry ID to process
        
    Returns:
        True if the entry was successfully assigned, False otherwise
    """
    # Import here to avoid circular imports
    from app.services.transfer_service import transfer_to_staff
    from app.models import AssignmentSource
    
    semaphore = _get_semaphore()
    
    try:
        async with semaphore:
            db = SessionLocal()
            try:
                entry = (
                    db.query(VisitorWaitingQueue)
                    .filter(
                        VisitorWaitingQueue.id == entry_id,
                        VisitorWaitingQueue.status == WaitingStatus.WAITING.value,
                    )
                    .first()
                )
                
                if not entry:
                    logger.debug(f"Queue entry {entry_id} not found or not waiting")
                    return False
                
                result = await transfer_to_staff(
                    db=db,
                    visitor_id=entry.visitor_id,
                    project_id=entry.project_id,
                    source=AssignmentSource.RULE,
                    visitor_message=entry.visitor_message,
                    session_id=entry.session_id,
                    notes=f"Immediate processing (entry_id={entry.id})",
                    skip_queue_status_check=True,
                    auto_commit=False,
                    ai_disabled=entry.ai_disabled,
                    add_to_queue_if_no_staff=False,  # Already in queue
                    expected_queue_entry_id=entry.id,
                )
                
                if result.success and result.assigned_staff_id:
                    db.commit()
                    logger.info(
                        f"Queue entry {entry_id} immediately assigned to staff {result.assigned_staff_id}",
                        extra={
                            "entry_id": str(entry_id),
                            "staff_id": str(result.assigned_staff_id),
                        }
                    )
                    return True
                else:
                    db.commit()  # Commit the attempt record
                    logger.debug(f"No staff available for immediate assignment of entry {entry_id}")
                    return False
                    
            finally:
                db.close()
                
    except Exception as e:
        logger.error(f"Error in immediate queue processing for entry {entry_id}: {e}")
        return False
