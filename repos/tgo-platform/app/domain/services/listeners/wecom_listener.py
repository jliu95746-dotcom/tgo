from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.db.models import Platform, WeComInbox, WeComSyncJob
from app.domain.entities import NormalizedMessage
from app.domain.ports import MessageNormalizer, TgoApiClient, SSEManager
from app.domain.services.dispatcher import process_message
from app.infra.visitor_client import VisitorService
from app.api.wecom_utils import (
    WeComSyncContinuation,
    get_wecom_visitor_profile,
    sync_kf_messages,
)


class WeComPlatformConfig(BaseModel):
    """Per-platform WeCom configuration stored in Platform.config when type='wecom'."""

    corp_id: str = ""     # 企业ID (required for wecom_kf, optional for wecom_bot)
    agent_id: str = ""    # 应用ID (required for wecom_kf, optional for wecom_bot)
    app_secret: str = ""  # 应用密钥 (required for wecom_kf, optional for wecom_bot)
    token: str = ""       # 回调签名 Token
    encoding_aes_key: str | None = None  # 消息加密密钥（可选）

    # Consumer processing configuration
    processing_batch_size: int = 10
    max_retry_attempts: int = 3
    consumer_poll_interval_seconds: int = 5
    processing_lease_seconds: int = 300


@dataclass
class _PlatformEntry:
    id: uuid.UUID
    project_id: uuid.UUID
    api_key: str | None
    cfg: WeComPlatformConfig
    platform_type: str  # "wecom" (KF) or "wecom_bot"


class WeComChannelListener:
    """WeCom consumer that processes pending wecom_inbox rows asynchronously.

    Producer: FastAPI callback endpoint stores messages into wecom_inbox.
    Consumer: this listener queries pending rows and processes them via dispatcher.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        normalizer: MessageNormalizer,
        tgo_api_client: TgoApiClient,
        sse_manager: SSEManager,
    ) -> None:
        self._session_factory = session_factory
        self._normalizer = normalizer
        self._tgo_api_client = tgo_api_client
        self._sse_manager = sse_manager
        self._stop_event = asyncio.Event()
        self._consumer_task: asyncio.Task | None = None
        self._visitor_service = VisitorService(
            base_url=settings.api_base_url,
            cache_ttl_seconds=300,
            redis_url=settings.redis_url,
        )
        self._visitor_service_closed = False

    async def start(self) -> None:
        if self._consumer_task is None or self._consumer_task.done():
            if self._stop_event.is_set():
                self._stop_event = asyncio.Event()
            if self._visitor_service_closed:
                self._visitor_service = VisitorService(
                    base_url=settings.api_base_url,
                    cache_ttl_seconds=300,
                    redis_url=settings.redis_url,
                )
                self._visitor_service_closed = False
            self._consumer_task = asyncio.create_task(self._consumer_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._consumer_task:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
        self._consumer_task = None
        if not self._visitor_service_closed:
            await self._visitor_service.aclose()
            self._visitor_service_closed = True

    async def _load_active_wecom_platforms(self) -> list[_PlatformEntry]:
        """Load all active WeCom platforms (both wecom_kf and wecom_bot types)."""
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(Platform.id, Platform.project_id, Platform.api_key, Platform.config, Platform.type)
                    .where(Platform.is_active.is_(True), Platform.type.in_(["wecom", "wecom_bot"]))
                )
            ).all()
        platforms: list[_PlatformEntry] = []
        for pid, project_id, api_key, cfg_dict, platform_type in rows:
            try:
                cfg = WeComPlatformConfig(**(cfg_dict or {}))
                platforms.append(_PlatformEntry(
                    id=pid,
                    project_id=project_id,
                    api_key=api_key,
                    cfg=cfg,
                    platform_type=platform_type or "wecom",
                ))
            except Exception as e:
                print(f"[WECOM] Skip platform {pid}: invalid config: {e}")
        return platforms

    async def _consumer_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                platforms = await self._load_active_wecom_platforms()
                for p in platforms:
                    try:
                        await self._process_sync_jobs_for_platform(p)
                        await self._process_pending_for_platform(p)
                    except Exception as e:
                        logging.exception("[WECOM] Consumer error for platform %s: %s", p.id, e)
                # Sleep using first platform's interval or default
                interval = platforms[0].cfg.consumer_poll_interval_seconds if platforms else 5
                await asyncio.sleep(max(1, int(interval)))
            except Exception as e:
                logging.exception("[WECOM] Consumer supervisor error: %s", e)
                await asyncio.sleep(5)


    # ---- Internal helper methods (refactor for clarity and reuse) ----
    async def _claim_next_record(
        self,
        session: AsyncSession,
        platform: _PlatformEntry,
        max_retries: int,
    ) -> WeComInbox | None:
        """Atomically claim one eligible record and establish a recovery lease."""
        now = datetime.now(timezone.utc)
        eligible = or_(
            WeComInbox.status == "pending",
            and_(
                WeComInbox.status == "failed",
                WeComInbox.retry_count <= max_retries,
                or_(
                    WeComInbox.next_attempt_at.is_(None),
                    WeComInbox.next_attempt_at <= now,
                ),
            ),
            and_(
                WeComInbox.status == "processing",
                or_(
                    WeComInbox.lease_expires_at.is_(None),
                    WeComInbox.lease_expires_at <= now,
                ),
            ),
        )
        try:
            record = await session.scalar(
                select(WeComInbox)
                .where(WeComInbox.platform_id == platform.id, eligible)
                .order_by(WeComInbox.fetched_at.asc())
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if record is None:
                return None
            lease_seconds = max(
                int(platform.cfg.processing_lease_seconds),
                int(settings.request_timeout_seconds) + 60,
            )
            record.status = "processing"
            record.error_message = None
            record.processing_started_at = now
            record.lease_expires_at = now + timedelta(seconds=lease_seconds)
            record.next_attempt_at = None
            await session.commit()
            return record
        except Exception as e:
            logging.exception("[WECOM] Claiming record failed: %s", e)
            await session.rollback()
            raise

    async def _claim_next_sync_job(
        self,
        session: AsyncSession,
        platform: _PlatformEntry,
        max_retries: int,
    ) -> WeComSyncJob | None:
        now = datetime.now(timezone.utc)
        eligible = or_(
            WeComSyncJob.status == "pending",
            and_(
                WeComSyncJob.status == "failed",
                WeComSyncJob.retry_count <= max_retries,
                or_(
                    WeComSyncJob.next_attempt_at.is_(None),
                    WeComSyncJob.next_attempt_at <= now,
                ),
            ),
            and_(
                WeComSyncJob.status == "processing",
                or_(
                    WeComSyncJob.lease_expires_at.is_(None),
                    WeComSyncJob.lease_expires_at <= now,
                ),
            ),
        )
        job = await session.scalar(
            select(WeComSyncJob)
            .where(WeComSyncJob.platform_id == platform.id, eligible)
            .order_by(WeComSyncJob.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return None
        lease_seconds = max(
            int(platform.cfg.processing_lease_seconds),
            int(settings.request_timeout_seconds) + 60,
        )
        job.status = "processing"
        job.error_message = None
        job.processing_started_at = now
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        job.next_attempt_at = None
        await session.commit()
        return job

    def _build_mapped_message(self, platform: _PlatformEntry, record: WeComInbox) -> dict[str, Any]:
        """Build the NormalizedMessage-like raw dict for downstream normalization."""
        # Determine source_type from record or fallback to platform_type
        source_type = getattr(record, "source_type", None) or ("wecom_bot" if platform.platform_type == "wecom_bot" else "wecom_kf")

        # Build WeCom-specific context used by adapter selection/sending
        wecom_ctx: dict[str, Any] = {
            "is_from_colleague": bool(record.is_from_colleague),
            "source_type": source_type,
        }

        if source_type == "wecom_kf":
            # WeCom Customer Service specific context
            try:
                raw_payload = record.raw_payload or {}
                # KF sync messages embed original msg at raw_payload["kf_sync_msg"], which includes open_kfid/external_userid
                kf_msg = raw_payload.get("kf_sync_msg") or {}
                open_kfid = kf_msg.get("open_kfid") or raw_payload.get("open_kfid") or record.open_kfid
                if open_kfid:
                    wecom_ctx["open_kfid"] = open_kfid
            except Exception:
                pass
            # external_userid is needed for KF send; extract via helper
            try:
                wecom_ctx["external_userid"] = self._extract_external_user_id(record)
            except Exception:
                pass
        else:
            # WeCom Bot specific context
            try:
                raw_payload = record.raw_payload or {}
                wecom_ctx["chat_id"] = raw_payload.get("chat_id") or record.open_kfid or ""
                wecom_ctx["chat_type"] = raw_payload.get("chat_type") or ""
                wecom_ctx["aibot_id"] = raw_payload.get("aibot_id") or ""
                # response_url is required for replying to wecom_bot messages
                wecom_ctx["response_url"] = raw_payload.get("response_url") or ""
            except Exception:
                pass

        message_type_map = {
            "text": 1,
            "image": 2,
            "file": 3,
            "voice": 4,
            "video": 5,
        }
        normalized_message_type = message_type_map.get((record.msg_type or "").lower(), 1)

        return {
            "source": "wecom",
            "from_uid": record.from_user,
            "content": record.content or "",
            "platform_api_key": platform.api_key or "",
            "platform_type": platform.platform_type,  # "wecom" or "wecom_bot"
            "platform_id": str(platform.id),
            "extra": {
                "project_id": str(platform.project_id),
                "message_id": record.message_id,
                "msg_type": normalized_message_type,
                "source_type": source_type,  # "wecom_kf" or "wecom_bot"
                "wecom": wecom_ctx,
            },
        }

    def _extract_external_user_id(self, record: WeComInbox) -> str:
        """Extract external_userid if present in raw_payload; fallback to from_user."""
        try:
            raw_payload = record.raw_payload or {}
            parsed = raw_payload.get("parsed") or {}
            return (
                parsed.get("ExternalUserID")
                or raw_payload.get("external_userid")
                or record.from_user
            )
        except Exception:
            return record.from_user

    async def _fetch_visitor_profile_cached(
        self,
        platform: _PlatformEntry,
        record: WeComInbox,
        external_user_id: str,
    ) -> tuple[str | None, str | None]:
        """Cache-first retrieval of visitor profile; calls WeCom APIs on cache miss."""
        display_name: str | None = None
        avatar_url: str | None = None
        try:
            cache_key = self._visitor_service.make_cache_key(str(platform.project_id), "wecom", record.from_user)
            cached = await self._visitor_service.get_cached(cache_key)
            if cached:
                display_name = cached.nickname or cached.name
                avatar_url = cached.avatar_url
            else:
                profile = await get_wecom_visitor_profile(
                    corp_id=platform.cfg.corp_id,
                    app_secret=platform.cfg.app_secret,
                    external_userid=external_user_id,
                )
                display_name = (profile or {}).get("nickname")
                avatar_url = (profile or {}).get("avatar")
        except Exception as e:
            print(f"[WECOM] Fetch visitor profile failed for {external_user_id}: {e}")
        return display_name, avatar_url

    def _attach_profile_to_extra(self, mapped_raw: dict[str, Any], display_name: str | None, avatar_url: str | None) -> None:
        """Attach visitor profile fields into mapped_raw.extra.visitor_profile safely."""
        try:
            extra = mapped_raw.get("extra") or {}
            extra["visitor_profile"] = {"nickname": display_name, "avatar_url": avatar_url}
            mapped_raw["extra"] = extra
        except Exception:
            pass

    async def _register_visitor(
        self,
        platform: _PlatformEntry,
        record: WeComInbox,
        display_name: str | None,
        avatar_url: str | None,
    ):
        """Register or get visitor through tgo-api, using cache in VisitorService."""
        if not platform.api_key:
            return None
        try:
            return await self._visitor_service.register_or_get(
                platform_api_key=platform.api_key,
                project_id=str(platform.project_id),
                platform_type="wecom",
                platform_open_id=record.from_user,
                nickname=display_name,
                avatar_url=avatar_url,
            )
        except Exception as e:
            print(f"[WECOM] Visitor registration failed for {platform.id}: {e}")
            return None

    async def _finalize_success(self, session: AsyncSession, record: WeComInbox, reply_text: str | None) -> None:
        """Mark record as completed with optional reply text."""
        record.ai_reply = reply_text
        record.status = "completed" if reply_text else "completed_no_reply"
        record.processed_at = datetime.now(timezone.utc)
        record.processing_started_at = None
        record.lease_expires_at = None
        record.next_attempt_at = None
        record.error_message = None
        await session.commit()

    async def _finalize_failure(
        self,
        session: AsyncSession,
        platform: _PlatformEntry,
        record: WeComInbox,
        error: Exception,
        max_retries: int,
    ) -> None:
        """Mark record as failed with retry increment and error message, preserving logs."""
        logging.error(
            "[WECOM] Processing failed for platform_id=%s message_id=%s: %s",
            platform.id,
            record.message_id,
            error,
        )
        now = datetime.now(timezone.utc)
        retry_count = int(record.retry_count or 0) + 1
        record.status = "dead" if retry_count > max_retries else "failed"
        record.processed_at = now
        record.processing_started_at = None
        record.lease_expires_at = None
        record.retry_count = retry_count
        record.next_attempt_at = (
            None
            if record.status == "dead"
            else now + timedelta(seconds=max(1, 2 ** (retry_count - 1)))
        )
        record.error_message = str(error)[:2000]
        await session.commit()

    async def _get_or_register_visitor(
        self,
        platform: _PlatformEntry,
        record: WeComInbox,
    ) -> tuple[Any | None, str | None, str | None]:
        """End-to-end flow for visitor retrieval/registration with minimal calls.

        Steps:
        1) Check VisitorService cache; if exists, return immediately (skip external calls)
        2) Else, fetch profile from WeCom (if possible, only for wecom_kf) to enrich nickname/avatar
        3) Register or get visitor via tgo-api using nickname/avatar; return result
        """
        display_name: str | None = None
        avatar_url: str | None = None
        visitor = None

        # Determine source type for platform-specific handling
        source_type = getattr(record, "source_type", None) or ("wecom_bot" if platform.platform_type == "wecom_bot" else "wecom_kf")
        platform_type_for_visitor = platform.platform_type  # "wecom" or "wecom_bot"

        try:
            cache_key = self._visitor_service.make_cache_key(str(platform.project_id), platform_type_for_visitor, record.from_user)
            cached = await self._visitor_service.get_cached(cache_key)
            if cached:
                display_name = cached.nickname or cached.name
                avatar_url = cached.avatar_url
                return cached, display_name, avatar_url
        except Exception as e:
            # Cache access errors shouldn't stop processing
            print(f"[WECOM] Visitor cache lookup failed for {platform.id}: {e}")

        # Cache miss: try to fetch profile from WeCom to enrich registration
        # Only for wecom_kf (customer service) - wecom_bot doesn't have external contact APIs
        if source_type == "wecom_kf" and platform.cfg.corp_id and platform.cfg.app_secret:
            external_user_id = self._extract_external_user_id(record)
            try:
                profile = await get_wecom_visitor_profile(
                    corp_id=platform.cfg.corp_id,
                    app_secret=platform.cfg.app_secret,
                    external_userid=external_user_id,
                )
                display_name = (profile or {}).get("nickname")
                avatar_url = (profile or {}).get("avatar")
            except Exception as e:
                print(f"[WECOM] Fetch visitor profile failed for {external_user_id}: {e}")
        elif source_type == "wecom_bot":
            # For wecom_bot, try to extract name from raw_payload
            try:
                raw_payload = record.raw_payload or {}
                parsed = raw_payload.get("parsed") or {}
                from_info = parsed.get("from") or {}
                if isinstance(from_info, dict):
                    display_name = from_info.get("name") or from_info.get("alias") or from_info.get("userid")
            except Exception:
                pass

        if platform.api_key:
            try:
                visitor = await self._visitor_service.register_or_get(
                    platform_api_key=platform.api_key,
                    project_id=str(platform.project_id),
                    platform_type=platform_type_for_visitor,
                    platform_open_id=record.from_user,
                    nickname=display_name,
                    avatar_url=avatar_url,
                )
            except Exception as e:
                print(f"[WECOM] Visitor registration failed for {platform.id}: {e}")
        return visitor, display_name, avatar_url


    async def _finalize_sync_job(
        self,
        job_id: uuid.UUID,
        error: Exception | None,
        max_retries: int,
    ) -> None:
        async with self._session_factory() as session:
            job = await session.get(WeComSyncJob, job_id)
            if job is None:
                raise RuntimeError(f"WeCom sync job {job_id} disappeared")
            now = datetime.now(timezone.utc)
            job.processing_started_at = None
            job.lease_expires_at = None
            if error is None:
                job.status = "completed"
                job.completed_at = now
                job.next_attempt_at = None
                job.error_message = None
            elif isinstance(error, WeComSyncContinuation):
                job.status = "pending"
                job.next_attempt_at = now
                job.error_message = None
            else:
                retry_count = int(job.retry_count or 0) + 1
                job.retry_count = retry_count
                job.status = "dead" if retry_count > max_retries else "failed"
                job.next_attempt_at = (
                    None
                    if job.status == "dead"
                    else now + timedelta(seconds=max(1, 2 ** (retry_count - 1)))
                )
                job.error_message = str(error)[:2000]
            await session.commit()

    async def _process_sync_jobs_for_platform(self, platform: _PlatformEntry) -> None:
        if platform.platform_type != "wecom":
            return
        batch_size = max(1, int(platform.cfg.processing_batch_size))
        max_retries = max(0, int(platform.cfg.max_retry_attempts))

        for _ in range(batch_size):
            async with self._session_factory() as claim_session:
                job = await self._claim_next_sync_job(
                    claim_session,
                    platform,
                    max_retries,
                )
                if job is None:
                    return
                job_id = job.id
                event_token = job.event_token
                open_kfid = job.open_kfid
                job_created_at = job.created_at

            try:
                if not (platform.cfg.corp_id and platform.cfg.app_secret):
                    raise RuntimeError("WeCom platform is missing corp_id or app_secret")
                if job_created_at.tzinfo is None:
                    job_created_at = job_created_at.replace(tzinfo=timezone.utc)
                token_age_seconds = (
                    datetime.now(timezone.utc) - job_created_at
                ).total_seconds()
                effective_event_token = (
                    event_token if token_age_seconds < 9 * 60 else ""
                )
                async with self._session_factory() as sync_session:
                    await sync_kf_messages(
                        corp_id=platform.cfg.corp_id,
                        app_secret=platform.cfg.app_secret,
                        event_token=effective_event_token,
                        open_kf_id=open_kfid,
                        platform_id=platform.id,
                        db=sync_session,
                    )
            except asyncio.CancelledError:
                await self._finalize_sync_job(
                    job_id,
                    RuntimeError("WeCom sync job was cancelled"),
                    max_retries,
                )
                raise
            except WeComSyncContinuation as exc:
                logging.info(
                    "[WECOM] KF sync will continue for platform_id=%s open_kfid=%s",
                    platform.id,
                    open_kfid,
                )
                await self._finalize_sync_job(job_id, exc, max_retries)
            except Exception as exc:
                logging.exception(
                    "[WECOM] KF sync job failed for platform_id=%s open_kfid=%s: %s",
                    platform.id,
                    open_kfid,
                    exc,
                )
                await self._finalize_sync_job(job_id, exc, max_retries)
            else:
                await self._finalize_sync_job(job_id, None, max_retries)

    async def _process_pending_for_platform(self, platform: _PlatformEntry) -> None:
        batch_size = max(1, int(platform.cfg.processing_batch_size))
        max_retries = max(0, int(platform.cfg.max_retry_attempts))

        async with self._session_factory() as db:
            for _ in range(batch_size):
                record = await self._claim_next_record(db, platform, max_retries)
                if record is None:
                    return

                try:
                    mapped_raw: dict[str, Any] = self._build_mapped_message(platform, record)
                    _, display_name, avatar_url = await self._get_or_register_visitor(
                        platform,
                        record,
                    )
                    self._attach_profile_to_extra(mapped_raw, display_name, avatar_url)

                    message: NormalizedMessage = await self._normalizer.normalize(mapped_raw)
                    reply_text = await process_message(
                        msg=message,
                        db=db,
                        tgo_api_client=self._tgo_api_client,
                        sse_manager=self._sse_manager,
                    )
                    await self._finalize_success(db, record, reply_text)
                except asyncio.CancelledError:
                    await self._finalize_failure(
                        db,
                        platform,
                        record,
                        RuntimeError("WeCom message processing was cancelled"),
                        max_retries,
                    )
                    raise
                except Exception as exc:
                    await self._finalize_failure(
                        db,
                        platform,
                        record,
                        exc,
                        max_retries,
                    )
