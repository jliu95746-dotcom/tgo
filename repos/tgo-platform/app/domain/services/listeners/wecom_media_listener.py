from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.api.wecom_utils import (
    invalidate_wecom_access_token,
    wecom_get_access_token,
)
from app.db.models import (
    MediaProcessingJob,
    MessageMedia,
    Platform,
    WeComInbox,
)
from app.domain.services.media.storage import MediaStorage
from app.domain.services.media.observability import (
    MediaMetricsSink,
    NoOpMediaMetrics,
    classify_media_failure,
)
from app.domain.services.media.types import (
    DownloadedMedia,
    MediaDownloadError,
    StoredMediaObject,
)
from app.domain.services.media.wecom_downloader import WeComMediaDownloader


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ClaimedMediaJob:
    job_id: uuid.UUID
    media_id: uuid.UUID
    inbox_id: uuid.UUID
    platform_id: uuid.UUID
    source_media_id: str
    media_type: str
    retry_count: int
    max_attempts: int
    claim_token: str
    previous_staging_object_key: str | None
    corp_id: str
    app_secret: str


class WeComMediaListener:
    """Consume bounded WeCom media download jobs with lease recovery."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        downloader: WeComMediaDownloader,
        storage: MediaStorage,
        metrics: MediaMetricsSink | None = None,
        max_concurrency: int | None = None,
    ) -> None:
        concurrency_limit = (
            settings.media_job_max_concurrency
            if max_concurrency is None
            else max_concurrency
        )
        if not 1 <= concurrency_limit <= 16:
            raise ValueError("media job concurrency must be between 1 and 16")
        self._session_factory = session_factory
        self._downloader = downloader
        self._storage = storage
        self._metrics = metrics or NoOpMediaMetrics(concurrency_limit=concurrency_limit)
        self._processing_slots = asyncio.Semaphore(concurrency_limit)
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
        await self._downloader.aclose()

    async def _consumer_loop(self) -> None:
        while not self._stop_event.is_set():
            processed = 0
            try:
                for _ in range(max(1, settings.media_job_batch_size)):
                    claimed = await self._claim_next_job()
                    if claimed is None:
                        break
                    processed += 1
                    await self._process_claimed_job(claimed)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "[WECOM_MEDIA] event=consumer_loop_failed error_type=%s",
                    type(exc).__name__,
                )
                await asyncio.sleep(max(1, settings.media_job_poll_interval_seconds))
                continue
            if processed == 0:
                await asyncio.sleep(max(1, settings.media_job_poll_interval_seconds))

    async def _claim_next_job(self) -> _ClaimedMediaJob | None:
        now = datetime.now(timezone.utc)
        eligible = or_(
            MediaProcessingJob.status == "pending",
            and_(
                MediaProcessingJob.status == "failed",
                MediaProcessingJob.retry_count < MediaProcessingJob.max_attempts,
                or_(
                    MediaProcessingJob.next_attempt_at.is_(None),
                    MediaProcessingJob.next_attempt_at <= now,
                ),
            ),
            and_(
                MediaProcessingJob.status == "processing",
                or_(
                    MediaProcessingJob.lease_expires_at.is_(None),
                    MediaProcessingJob.lease_expires_at <= now,
                ),
            ),
        )
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(MediaProcessingJob, MessageMedia, WeComInbox, Platform)
                    .join(MessageMedia, MessageMedia.id == MediaProcessingJob.media_id)
                    .join(WeComInbox, WeComInbox.id == MessageMedia.inbox_id)
                    .join(Platform, Platform.id == MessageMedia.platform_id)
                    .where(
                        MediaProcessingJob.job_type == "download",
                        MessageMedia.media_type.in_(["image", "voice"]),
                        Platform.is_active.is_(True),
                        eligible,
                    )
                    .order_by(MediaProcessingJob.created_at.asc())
                    .with_for_update(of=MediaProcessingJob, skip_locked=True)
                    .limit(1)
                )
            ).first()
            if row is None:
                return None
            job, media, inbox, platform = row
            platform_config = (
                platform.config if isinstance(platform.config, dict) else {}
            )
            corp_id = str(platform_config.get("corp_id") or "").strip()
            app_secret = str(platform_config.get("app_secret") or "").strip()
            previous_staging_object_key = job.staging_object_key
            claim_token = uuid.uuid4().hex
            lease_seconds = max(
                settings.media_job_lease_seconds,
                settings.media_download_timeout_seconds + 60,
                settings.media_job_total_timeout_seconds + 30,
            )
            job.status = "processing"
            job.claim_token = claim_token
            job.processing_started_at = now
            job.lease_expires_at = now + timedelta(seconds=lease_seconds)
            job.next_attempt_at = None
            job.error_code = None
            job.error_message = None
            media.status = "downloading"
            media.error_code = None
            media.error_message = None
            inbox.status = "media_downloading"
            inbox.error_message = None
            await session.commit()
            return _ClaimedMediaJob(
                job_id=job.id,
                media_id=media.id,
                inbox_id=inbox.id,
                platform_id=platform.id,
                source_media_id=media.source_media_id,
                media_type=media.media_type,
                retry_count=int(job.retry_count or 0),
                max_attempts=int(job.max_attempts or settings.media_job_max_attempts),
                claim_token=claim_token,
                previous_staging_object_key=previous_staging_object_key,
                corp_id=corp_id,
                app_secret=app_secret,
            )

    async def _process_claimed_job(self, claimed: _ClaimedMediaJob) -> None:
        async with self._processing_slots:
            self._metrics.job_started(claimed.media_type)
            try:
                await self._process_claimed_job_in_slot(claimed)
            finally:
                self._metrics.job_finished()

    async def _process_claimed_job_in_slot(self, claimed: _ClaimedMediaJob) -> None:
        try:
            async with asyncio.timeout(settings.media_job_total_timeout_seconds):
                if claimed.previous_staging_object_key is not None:
                    await self._storage.delete(
                        object_key=claimed.previous_staging_object_key
                    )
                    if not await self._clear_previous_staging(claimed):
                        self._record_job_failure(
                            claimed,
                            code="claim_lost",
                            retryable=False,
                        )
                        return
                if not (claimed.corp_id and claimed.app_secret):
                    raise MediaDownloadError(
                        "platform_config_invalid",
                        "WeCom platform is missing corp_id or app_secret",
                        retryable=False,
                    )
                access_token = await wecom_get_access_token(
                    claimed.corp_id,
                    claimed.app_secret,
                    timeout=settings.media_download_timeout_seconds,
                )
                max_bytes = (
                    settings.media_image_max_bytes
                    if claimed.media_type == "image"
                    else settings.media_voice_max_bytes
                )
                try:
                    downloaded = await self._downloader.download(
                        access_token=access_token,
                        source_media_id=claimed.source_media_id,
                        media_type=claimed.media_type,
                        max_bytes=max_bytes,
                        max_image_pixels=settings.media_image_max_pixels,
                        max_image_frames=settings.media_image_max_frames,
                        max_voice_duration_seconds=(
                            settings.media_voice_max_duration_seconds
                        ),
                    )
                except MediaDownloadError as exc:
                    if exc.code not in {"wecom_40014", "wecom_42001"}:
                        raise
                    await invalidate_wecom_access_token(
                        claimed.corp_id,
                        claimed.app_secret,
                    )
                    refreshed_token = await wecom_get_access_token(
                        claimed.corp_id,
                        claimed.app_secret,
                        timeout=settings.media_download_timeout_seconds,
                    )
                    downloaded = await self._downloader.download(
                        access_token=refreshed_token,
                        source_media_id=claimed.source_media_id,
                        media_type=claimed.media_type,
                        max_bytes=max_bytes,
                        max_image_pixels=settings.media_image_max_pixels,
                        max_image_frames=settings.media_image_max_frames,
                        max_voice_duration_seconds=(
                            settings.media_voice_max_duration_seconds
                        ),
                    )
                object_key = self._storage.object_key_for(
                    platform_id=claimed.platform_id,
                    inbox_id=claimed.inbox_id,
                    attempt_id=claimed.claim_token,
                    media=downloaded,
                )
                if not await self._record_staging_object(claimed, object_key):
                    self._record_job_failure(
                        claimed,
                        code="claim_lost",
                        retryable=False,
                    )
                    return
                stored = await self._storage.put(
                    object_key=object_key,
                    media=downloaded,
                )
        except asyncio.CancelledError:
            await self._release_claim(claimed)
            raise
        except TimeoutError:
            self._record_job_failure(
                claimed,
                code="processing_timeout",
                retryable=True,
                timed_out=True,
            )
            await self._finalize_failure(
                claimed,
                code="processing_timeout",
                message="Media download exceeded the total processing deadline",
                retryable=True,
            )
        except MediaDownloadError as exc:
            self._record_job_failure(
                claimed,
                code=exc.code,
                retryable=exc.retryable,
            )
            await self._finalize_failure(
                claimed,
                code=exc.code,
                message=str(exc),
                retryable=exc.retryable,
            )
        except Exception as exc:
            logger.error(
                "[WECOM_MEDIA] event=processing_failed error_type=%s",
                type(exc).__name__,
            )
            self._record_job_failure(
                claimed,
                code="processing_error",
                retryable=True,
            )
            await self._finalize_failure(
                claimed,
                code="processing_error",
                message="Media storage or processing failed",
                retryable=True,
            )
        else:
            try:
                finalized = await self._finalize_success(
                    claimed,
                    downloaded,
                    stored,
                )
            except Exception as exc:
                logger.error(
                    "[WECOM_MEDIA] event=database_finalize_failed error_type=%s",
                    type(exc).__name__,
                )
                self._record_job_failure(
                    claimed,
                    code="database_finalize_error",
                    retryable=True,
                )
                await self._compensate_finalize_failure(claimed, stored)
            else:
                if not finalized:
                    logger.warning("[WECOM_MEDIA] event=claim_changed_before_finalize")
                    self._record_job_failure(
                        claimed,
                        code="claim_lost",
                        retryable=False,
                    )
                    await self._delete_uncommitted_object(stored)
                else:
                    self._metrics.job_succeeded(claimed.media_type)

    def _record_job_failure(
        self,
        claimed: _ClaimedMediaJob,
        *,
        code: str,
        retryable: bool,
        timed_out: bool = False,
    ) -> None:
        retry_scheduled = retryable and claimed.retry_count + 1 < claimed.max_attempts
        self._metrics.job_failed(
            claimed.media_type,
            failure_category=classify_media_failure(code),
            retry_scheduled=retry_scheduled,
            timed_out=timed_out,
        )

    async def _clear_previous_staging(self, claimed: _ClaimedMediaJob) -> bool:
        async with self._session_factory() as session:
            job_id = await session.scalar(
                update(MediaProcessingJob)
                .where(
                    MediaProcessingJob.id == claimed.job_id,
                    MediaProcessingJob.claim_token == claimed.claim_token,
                    MediaProcessingJob.status == "processing",
                    MediaProcessingJob.staging_object_key
                    == claimed.previous_staging_object_key,
                )
                .values(staging_object_key=None)
                .returning(MediaProcessingJob.id)
            )
            if job_id is None:
                await session.rollback()
                return False
            await session.commit()
            return True

    async def _record_staging_object(
        self,
        claimed: _ClaimedMediaJob,
        object_key: str,
    ) -> bool:
        async with self._session_factory() as session:
            job_id = await session.scalar(
                update(MediaProcessingJob)
                .where(
                    MediaProcessingJob.id == claimed.job_id,
                    MediaProcessingJob.claim_token == claimed.claim_token,
                    MediaProcessingJob.status == "processing",
                    MediaProcessingJob.staging_object_key.is_(None),
                )
                .values(staging_object_key=object_key)
                .returning(MediaProcessingJob.id)
            )
            if job_id is None:
                await session.rollback()
                return False
            await session.commit()
            return True

    async def _finalize_success(
        self,
        claimed: _ClaimedMediaJob,
        downloaded: DownloadedMedia,
        stored: StoredMediaObject,
    ) -> bool:
        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            claimed_job_id = await session.scalar(
                update(MediaProcessingJob)
                .where(
                    MediaProcessingJob.id == claimed.job_id,
                    MediaProcessingJob.claim_token == claimed.claim_token,
                    MediaProcessingJob.status == "processing",
                )
                .values(
                    status="completed",
                    claim_token=None,
                    staging_object_key=None,
                    processing_started_at=None,
                    lease_expires_at=None,
                    next_attempt_at=None,
                    completed_at=now,
                    error_code=None,
                    error_message=None,
                )
                .returning(MediaProcessingJob.id)
            )
            if claimed_job_id is None:
                await session.rollback()
                logger.warning("[WECOM_MEDIA] event=claim_lost_during_success_finalize")
                return False
            await session.execute(
                update(MessageMedia)
                .where(MessageMedia.id == claimed.media_id)
                .values(
                    status="downloaded",
                    storage_provider=stored.provider,
                    object_key=stored.object_key,
                    byte_size=len(downloaded.content),
                    mime_type=downloaded.mime_type,
                    sha256=downloaded.sha256,
                    encryption_mode=stored.encryption_mode,
                    encryption_key_id=stored.encryption_key_id,
                    retention_until=now + timedelta(days=settings.media_retention_days),
                    downloaded_at=now,
                    error_code=None,
                    error_message=None,
                )
            )
            await session.execute(
                update(WeComInbox)
                .where(WeComInbox.id == claimed.inbox_id)
                .values(status="media_downloaded", error_message=None)
            )
            await session.commit()
            return True

    async def _compensate_finalize_failure(
        self,
        claimed: _ClaimedMediaJob,
        stored: StoredMediaObject,
    ) -> None:
        try:
            async with self._session_factory() as session:
                committed_job_id = await session.scalar(
                    select(MediaProcessingJob.id)
                    .join(
                        MessageMedia,
                        MessageMedia.id == MediaProcessingJob.media_id,
                    )
                    .where(
                        MediaProcessingJob.id == claimed.job_id,
                        MediaProcessingJob.status == "completed",
                        MessageMedia.object_key == stored.object_key,
                    )
                )
        except Exception as exc:
            logger.error(
                "[WECOM_MEDIA] event=finalize_outcome_check_failed error_type=%s",
                type(exc).__name__,
            )
            return
        if committed_job_id is not None:
            return
        await self._delete_uncommitted_object(stored)
        await self._finalize_failure(
            claimed,
            code="database_finalize_error",
            message="Media metadata finalization failed",
            retryable=True,
        )

    async def _delete_uncommitted_object(
        self,
        stored: StoredMediaObject,
    ) -> None:
        try:
            await self._storage.delete(object_key=stored.object_key)
        except Exception as exc:
            logger.error(
                "[WECOM_MEDIA] event=uncommitted_cleanup_failed error_type=%s",
                type(exc).__name__,
            )

    async def _finalize_failure(
        self,
        claimed: _ClaimedMediaJob,
        *,
        code: str,
        message: str,
        retryable: bool,
    ) -> None:
        retry_count = claimed.retry_count + 1
        dead = not retryable or retry_count >= claimed.max_attempts
        now = datetime.now(timezone.utc)
        next_attempt_at = (
            None
            if dead
            else now + timedelta(seconds=min(60, max(1, 2 ** (retry_count - 1))))
        )
        async with self._session_factory() as session:
            claimed_job_id = await session.scalar(
                update(MediaProcessingJob)
                .where(
                    MediaProcessingJob.id == claimed.job_id,
                    MediaProcessingJob.claim_token == claimed.claim_token,
                    MediaProcessingJob.status == "processing",
                )
                .values(
                    status="dead" if dead else "failed",
                    retry_count=retry_count,
                    claim_token=None,
                    processing_started_at=None,
                    lease_expires_at=None,
                    next_attempt_at=next_attempt_at,
                    error_code=code,
                    error_message=message[:2000],
                )
                .returning(MediaProcessingJob.id)
            )
            if claimed_job_id is None:
                await session.rollback()
                return
            await session.execute(
                update(MessageMedia)
                .where(MessageMedia.id == claimed.media_id)
                .values(
                    status="failed" if dead else "retrying",
                    error_code=code,
                    error_message=message[:2000],
                )
            )
            await session.execute(
                update(WeComInbox)
                .where(WeComInbox.id == claimed.inbox_id)
                .values(
                    status="media_failed" if dead else "pending_media",
                    error_message=message[:2000],
                )
            )
            await session.commit()

    async def _release_claim(self, claimed: _ClaimedMediaJob) -> None:
        async with self._session_factory() as session:
            claimed_job_id = await session.scalar(
                update(MediaProcessingJob)
                .where(
                    MediaProcessingJob.id == claimed.job_id,
                    MediaProcessingJob.claim_token == claimed.claim_token,
                )
                .values(
                    status="pending",
                    claim_token=None,
                    processing_started_at=None,
                    lease_expires_at=None,
                    next_attempt_at=None,
                )
                .returning(MediaProcessingJob.id)
            )
            if claimed_job_id is not None:
                await session.execute(
                    update(MessageMedia)
                    .where(MessageMedia.id == claimed.media_id)
                    .values(status="pending")
                )
                await session.execute(
                    update(WeComInbox)
                    .where(WeComInbox.id == claimed.inbox_id)
                    .values(status="pending_media")
                )
                await session.commit()
            else:
                await session.rollback()
