"""Platform synchronization scheduling and event triggers.

This module exposes:
- trigger_platform_sync: enqueue a sync job for a Platform (create/update)
- trigger_platform_delete: enqueue a delete sync job for a Platform
- start_sync_monitor: start background tasks to process queue and retry

It maintains minimal, dependency-free scheduling using asyncio tasks
(kicks off in app.main startup_event). In multi-process deployments, migrate
this to a shared work queue (e.g., Redis, Celery) for robustness.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.config import settings
from app.models.platform import Platform, PlatformType
from app.services.platform_sync_client import platform_sync_client

logger = logging.getLogger(__name__)


@dataclass
class PlatformSyncJob:
    platform_id: str
    action: str  # 'upsert' | 'delete'


_queue: "asyncio.Queue[PlatformSyncJob]" = asyncio.Queue()
_consumer_task: Optional[asyncio.Task] = None
_retry_task: Optional[asyncio.Task] = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_aware(dt: datetime) -> datetime:
    """Normalize a datetime to timezone-aware UTC for safe arithmetic."""
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _mark_status(
    db: Session,
    platform: Platform,
    status: str,
    *,
    error: Optional[str] = None,
    inc_retry: bool = False,
) -> None:
    platform.sync_status = status
    if status == "synced":
        platform.last_synced_at = _now()
        platform.sync_error = None
        platform.sync_retry_count = 0
    else:
        if error:
            platform.sync_error = (error or "")[:1000]
        if inc_retry:
            platform.sync_retry_count = (platform.sync_retry_count or 0) + 1
    db.add(platform)
    db.commit()


def trigger_platform_sync(platform_id: str) -> None:
    try:
        _queue.put_nowait(
            PlatformSyncJob(platform_id=platform_id, action="upsert")
        )
    except Exception:
        logger.exception(
            "Failed to enqueue platform sync",
            extra={"platform_id": platform_id},
        )


def trigger_platform_delete(platform_id: str) -> None:
    try:
        _queue.put_nowait(
            PlatformSyncJob(platform_id=platform_id, action="delete")
        )
    except Exception:
        logger.exception(
            "Failed to enqueue platform delete",
            extra={"platform_id": platform_id},
        )


async def _process_job(job: PlatformSyncJob) -> None:
    db: Session = SessionLocal()
    try:
        platform: Optional[Platform] = db.get(Platform, job.platform_id)
        # For delete jobs, platform may be None if already hard-deleted locally

        if job.action == "delete":
            if platform is None:
                # attempt remote delete if possible

                try:
                    await platform_sync_client.delete_platform(job.platform_id)
                except Exception as exc:
                    logger.warning(
                        "Remote delete failed",
                        extra={
                            "platform_id": job.platform_id,
                            "error": str(exc),
                        },
                    )
                return
        if not platform:
            return
        if platform.type == PlatformType.WECHAT_LOCAL.value:
            _mark_status(db, platform, "synced")
            return
        # Mark pending (avoid infinite loops by only changing sync fields)

        platform.sync_status = "pending"
        platform.sync_error = None
        db.add(platform)
        db.commit()

        # Prepare record data

        data = {
            "id": str(platform.id),
            "project_id": str(platform.project_id),
            "name": platform.name,
            "type": platform.type,
            "config": platform.config,
            "is_active": platform.is_active and platform.deleted_at is None,
            "api_key": platform.api_key,
            "created_at": platform.created_at,
            "updated_at": platform.updated_at,
            "deleted_at": platform.deleted_at,
        }

        # Upsert via Platform Service

        resp = await platform_sync_client.upsert_platform(data)
        ok = 200 <= resp.status_code < 300
        if ok:
            _mark_status(db, platform, "synced")
        else:
            _mark_status(
                db,
                platform,
                "failed",
                error=f"HTTP {resp.status_code}: {resp.text}",
                inc_retry=True,
            )
    except Exception as exc:
        logger.exception(
            "Sync job failed", extra={"platform_id": job.platform_id}
        )
        # Best-effort mark failed when possible

        try:
            if 'db' in locals():
                platform = locals().get('platform')
                if platform is not None:
                    _mark_status(
                        db, platform, "failed", error=str(exc), inc_retry=True
                    )
        except Exception:
            pass
    finally:
        db.close()


async def _consumer_loop() -> None:
    while True:
        job = await _queue.get()
        await _process_job(job)
        _queue.task_done()


async def _retry_loop() -> None:
    """Periodically scan for platforms that need retry and enqueue jobs.

    Avoids tight coupling to SQLAlchemy events; useful if events were missed.
    """
    while True:
        await asyncio.sleep(settings.PLATFORM_SYNC_RETRY_INTERVAL_SECONDS)
        db: Session = SessionLocal()
        try:
            # fetch a limited batch to avoid long scans

            rows = (
                db.query(Platform)
                .filter(Platform.sync_status != "synced")
                .limit(settings.PLATFORM_SYNC_BATCH_LIMIT)
                .all()
            )
            for p in rows:
                # backoff check

                retry_count = p.sync_retry_count or 0
                # exponential backoff base interval

                base = settings.PLATFORM_SYNC_RETRY_INTERVAL_SECONDS
                delay = base * (2 ** min(retry_count, 5))
                last: Optional[datetime] = (
                    p.last_synced_at or p.updated_at or p.created_at
                )
                if last is None:
                    last = _now()
                else:
                    last = _to_aware(last)
                # If enough time passed, enqueue

                if (_now() - last).total_seconds() >= delay:
                    trigger_platform_sync(str(p.id))
        except Exception:
            logger.exception("Retry loop failed")
        finally:
            db.close()


def start_sync_monitor() -> None:
    global _consumer_task, _retry_task
    if _consumer_task is None or _consumer_task.done():
        _consumer_task = asyncio.create_task(_consumer_loop())
        logger.info("Platform sync consumer started")
    if _retry_task is None or _retry_task.done():
        _retry_task = asyncio.create_task(_retry_loop())
        logger.info("Platform sync retry started")
