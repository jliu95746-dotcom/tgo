"""Periodic task to sync visitor online status with WuKongIM."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.models import Visitor
from app.services.wukongim_client import wukongim_client

logger = get_logger("tasks.sync_visitor_online_status")

# Global state
_task: Optional[asyncio.Task] = None
_processing_lock = asyncio.Lock()


async def _process_online_status_sync() -> int:
    (
        "\n"
        "    Sync visitor online status with WuKongIM.\n"
        "    \n"
        "    Only checks visitors who are currently marked as online in the "
        "database.\n"
        "    \n"
        "    Returns:\n"
        "        Number of visitors marked offline\n"
        "    "
    )
    db: Session = SessionLocal()
    marked_offline_count = 0

    try:
        # 1) Find all visitors currently marked as online in DB
        online_visitors = (
            db.query(Visitor).filter(Visitor.is_online == True).all()
        )

        if not online_visitors:
            return 0

        logger.debug(
            f"Found {len(online_visitors)} visitors marked as online in DB"
        )

        # 2) Batch processing
        batch_size = settings.VISITOR_ONLINE_SYNC_BATCH_SIZE
        for i in range(0, len(online_visitors), batch_size):
            batch = online_visitors[i: i + batch_size]

            # Map visitor ID to UID (WuKongIM uses "{visitor_id}-vtr")
            uid_to_visitor = {f"{v.id}-vtr": v for v in batch}
            uids = list(uid_to_visitor.keys())

            try:
                # 3) Check real online status from WuKongIM
                # This returns a list of UIDs that are ACTUALLY online
                actually_online_uids = (
                    await wukongim_client.check_user_online_status(uids)
                )
                actually_online_set = set(actually_online_uids)

                # 4) Identify visitors who are actually offline
                any_changes = False
                for uid, visitor in uid_to_visitor.items():
                    if uid not in actually_online_set:
                        # This visitor is marked online in DB but is offline in
                        # WuKongIM
                        visitor.is_online = False
                        visitor.last_offline_time = datetime.utcnow()
                        marked_offline_count += 1
                        any_changes = True

                        logger.info(
                            (
                                f"Visitor "
                                f"{visitor.id}"
                                f" corrected to offline (sync)"
                            ),
                            extra={"visitor_id": str(visitor.id)},
                        )

                if any_changes:
                    db.commit()

            except Exception as e:
                logger.error(
                    f"Error syncing batch of visitor online status: {e}"
                )
                # Continue with next batch

        return marked_offline_count

    except Exception as e:
        logger.error(f"Error in visitor online status sync process: {e}")
        return marked_offline_count
    finally:
        db.close()


async def _run_periodic_task() -> None:
    """Run the periodic online status sync."""
    logger.info(
        f"Starting visitor online status sync task "
        f"(interval={settings.VISITOR_ONLINE_SYNC_INTERVAL_SECONDS}s)"
    )

    while True:
        try:
            async with _processing_lock:
                corrected_count = await _process_online_status_sync()
                if corrected_count > 0:
                    logger.info(
                        f"Corrected {corrected_count} visitors to offline"
                    )
        except Exception as e:
            logger.error(f"Error in periodic online status sync: {e}")

        await asyncio.sleep(settings.VISITOR_ONLINE_SYNC_INTERVAL_SECONDS)


async def start_visitor_online_sync_task() -> None:
    """Start the background visitor online status sync task."""
    global _task

    if not settings.VISITOR_ONLINE_SYNC_ENABLED:
        logger.info("Visitor online status sync is disabled")
        return

    if _task is not None and not _task.done():
        logger.warning("Visitor online status sync task is already running")
        return

    _task = asyncio.create_task(_run_periodic_task())
    logger.info("Visitor online status sync task started")


async def stop_visitor_online_sync_task() -> None:
    """Stop the background visitor online status sync task."""
    global _task

    if _task is None:
        return

    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass

    _task = None
    logger.info("Visitor online status sync task stopped")


async def trigger_online_status_sync() -> int:
    (
        "\n"
        "    Manually trigger a visitor online status sync.\n"
        "    \n"
        "    Returns:\n"
        "        Number of visitors corrected to offline\n"
        "    "
    )
    async with _processing_lock:
        return await _process_online_status_sync()
