from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

from sqlalchemy import or_

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.models.project_ai_config import ProjectAIConfig
from app.services.project_ai_config_sync import sync_configs_with_retry

logger = get_logger("tasks.sync_project_ai_configs")

_task: Optional[asyncio.Task] = None


async def _sync_pending_failed_once() -> None:
    """Scan DB for ProjectAIConfig needing sync and perform batch sync with retry."""
    db = SessionLocal()
    try:
        q = db.query(ProjectAIConfig).filter(
            ProjectAIConfig.deleted_at.is_(None),
            or_(
                ProjectAIConfig.sync_status.in_(["failed", "pending"]),
                ProjectAIConfig.last_synced_at.is_(None),
            ),
        )
        items = q.all()
        total = len(items)
        if total == 0:
            logger.debug("ProjectAIConfig periodic sync: no items to sync")
            return

        logger.info(
            "ProjectAIConfig periodic sync start", extra={"count": total}
        )

        ok, err, _ = await sync_configs_with_retry(items)
        now = datetime.utcnow()
        if ok:
            for it in items:
                it.last_synced_at = now
                it.sync_status = "synced"
                it.sync_error = None
            db.commit()
            logger.info(
                "ProjectAIConfig periodic sync done",
                extra={"count": total, "synced": total, "failed": 0},
            )
        else:
            err_msg = str(err) if err else "unknown error"
            for it in items:
                it.last_synced_at = now
                it.sync_status = "failed"
                it.sync_error = err_msg
            db.commit()
            logger.warning(
                "ProjectAIConfig periodic sync failed",
                extra={
                    "count": total,
                    "synced": 0,
                    "failed": total,
                    "error": err_msg,
                },
            )
    except Exception as e:
        logger.exception(
            "ProjectAIConfig periodic sync exception", extra={"error": str(e)}
        )
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()


async def _loop() -> None:
    interval_sec = max(
        1, int(settings.PROJECT_AI_CONFIG_SYNC_INTERVAL_MINUTES) * 60
    )
    while True:
        await _sync_pending_failed_once()
        await asyncio.sleep(interval_sec)


def start_project_ai_config_sync_task() -> None:
    """Start periodic background task if enabled."""
    global _task
    if not settings.PROJECT_AI_CONFIG_SYNC_ENABLED:
        logger.info("ProjectAIConfig periodic sync disabled by config")
        return
    if _task and not _task.done():
        return
    try:
        _task = asyncio.create_task(_loop())
        logger.info(
            "ProjectAIConfig periodic sync task started",
            extra={
                "interval_minutes": settings.PROJECT_AI_CONFIG_SYNC_INTERVAL_MINUTES
            },
        )
    except Exception as e:
        logger.warning(
            "Failed to start ProjectAIConfig periodic sync task",
            extra={"error": str(e)},
        )


async def stop_project_ai_config_sync_task() -> None:
    global _task
    if _task:
        _task.cancel()
        try:
            await _task
        except Exception:
            pass
        _task = None
        logger.info("ProjectAIConfig periodic sync task stopped")
