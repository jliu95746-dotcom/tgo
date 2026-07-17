from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.db.models import MediaProcessingJob, MessageMedia
from app.domain.services.media.observability import (
    MediaCleanupKind,
    MediaMetricsSink,
    NoOpMediaMetrics,
)
from app.domain.services.media.storage import MediaObjectDeleter


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ExpiredMedia:
    media_id: uuid.UUID
    object_key: str


class MediaCleanupListener:
    """Clean expired committed media and crash-orphaned staging objects."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        object_deleter: MediaObjectDeleter,
        metrics: MediaMetricsSink | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._object_deleter = object_deleter
        self._metrics = metrics or NoOpMediaMetrics()
        self._stop_event = asyncio.Event()
        self._consumer_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._consumer_task is None or self._consumer_task.done():
            if self._stop_event.is_set():
                self._stop_event = asyncio.Event()
            self._consumer_task = asyncio.create_task(self._consumer_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._consumer_task is not None:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
        self._consumer_task = None

    async def _consumer_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._cleanup_staging_objects()
                await self._cleanup_expired_media()
                self._metrics.cleanup_finished(
                    MediaCleanupKind.CYCLE,
                    succeeded=True,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "[MEDIA_CLEANUP] event=cycle_failed error_type=%s",
                    type(exc).__name__,
                )
                self._metrics.cleanup_finished(
                    MediaCleanupKind.CYCLE,
                    succeeded=False,
                )
            await asyncio.sleep(settings.media_cleanup_interval_seconds)

    async def _cleanup_staging_objects(self) -> None:
        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            jobs = (
                (
                    await session.execute(
                        select(MediaProcessingJob)
                        .where(
                            MediaProcessingJob.staging_object_key.is_not(None),
                            or_(
                                MediaProcessingJob.status != "processing",
                                MediaProcessingJob.lease_expires_at.is_(None),
                                MediaProcessingJob.lease_expires_at <= now,
                            ),
                        )
                        .order_by(MediaProcessingJob.updated_at.asc())
                        .with_for_update(skip_locked=True)
                        .limit(settings.media_cleanup_batch_size)
                    )
                )
                .scalars()
                .all()
            )
            for job in jobs:
                object_key = job.staging_object_key
                if object_key is None:
                    continue
                try:
                    await self._object_deleter.delete(object_key=object_key)
                except Exception as exc:
                    logger.error(
                        "[MEDIA_CLEANUP] event=staging_delete_failed error_type=%s",
                        type(exc).__name__,
                    )
                    self._metrics.cleanup_finished(
                        MediaCleanupKind.STAGING,
                        succeeded=False,
                    )
                else:
                    job.staging_object_key = None
                    self._metrics.cleanup_finished(
                        MediaCleanupKind.STAGING,
                        succeeded=True,
                    )
            await session.commit()

    async def _cleanup_expired_media(self) -> None:
        now = datetime.now(timezone.utc)
        stale_cutoff = now - timedelta(seconds=settings.media_job_lease_seconds)
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(MessageMedia)
                        .where(
                            MessageMedia.object_key.is_not(None),
                            or_(
                                and_(
                                    MessageMedia.status == "downloaded",
                                    MessageMedia.retention_until.is_not(None),
                                    MessageMedia.retention_until <= now,
                                ),
                                MessageMedia.status == "delete_failed",
                                and_(
                                    MessageMedia.status == "deleting",
                                    MessageMedia.updated_at <= stale_cutoff,
                                ),
                            ),
                        )
                        .order_by(MessageMedia.retention_until.asc())
                        .with_for_update(skip_locked=True)
                        .limit(settings.media_cleanup_batch_size)
                    )
                )
                .scalars()
                .all()
            )
            expired = [
                _ExpiredMedia(media_id=row.id, object_key=str(row.object_key))
                for row in rows
                if row.object_key
            ]
            for row in rows:
                row.status = "deleting"
                row.updated_at = now
                row.error_code = None
                row.error_message = None
            await session.commit()

        for media in expired:
            try:
                await self._object_deleter.delete(object_key=media.object_key)
            except Exception as exc:
                logger.error(
                    "[MEDIA_CLEANUP] event=retention_delete_failed error_type=%s",
                    type(exc).__name__,
                )
                self._metrics.cleanup_finished(
                    MediaCleanupKind.RETENTION,
                    succeeded=False,
                )
                async with self._session_factory() as session:
                    await session.execute(
                        update(MessageMedia)
                        .where(
                            MessageMedia.id == media.media_id,
                            MessageMedia.status == "deleting",
                        )
                        .values(
                            status="delete_failed",
                            error_code="storage_delete_error",
                            error_message="Encrypted media deletion failed",
                        )
                    )
                    await session.commit()
            else:
                self._metrics.cleanup_finished(
                    MediaCleanupKind.RETENTION,
                    succeeded=True,
                )
                async with self._session_factory() as session:
                    await session.execute(
                        update(MessageMedia)
                        .where(
                            MessageMedia.id == media.media_id,
                            MessageMedia.status == "deleting",
                        )
                        .values(
                            status="deleted",
                            object_key=None,
                            deleted_at=now,
                            error_code=None,
                            error_message=None,
                        )
                    )
                    await session.commit()
