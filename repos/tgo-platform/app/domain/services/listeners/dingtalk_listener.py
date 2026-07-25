"""DingTalk Bot message consumer.

This module implements a producer-consumer pattern for DingTalk Bot messages:
- Producer: FastAPI callback endpoint stores messages into DingTalkInbox
- Consumer: This listener polls pending rows and processes them via dispatcher
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.db.models import Platform, DingTalkInbox
from app.domain.entities import NormalizedMessage
from app.domain.ports import MessageNormalizer, TgoApiClient, SSEManager
from app.domain.services.dispatcher import process_message
from app.domain.services.inbox_reliability import (
    claim_inbox_batch,
    finalize_inbox_failure,
    finalize_inbox_success,
)
from app.infra.visitor_client import VisitorService


class DingTalkPlatformConfig(BaseModel):
    """Per-platform DingTalk Bot configuration stored in Platform.config when type='dingtalk_bot'."""

    app_key: str = ""         # 应用Key
    app_secret: str = ""      # 应用Secret
    robot_code: str = ""      # 机器人编码
    aes_key: str | None = None  # 消息加密密钥（可选）
    token: str | None = None    # 签名 Token（可选）

    # Consumer processing configuration
    processing_batch_size: int = 10
    max_retry_attempts: int = 3
    consumer_poll_interval_seconds: int = 5


@dataclass
class _PlatformEntry:
    id: uuid.UUID
    project_id: uuid.UUID
    api_key: str | None
    cfg: DingTalkPlatformConfig


class DingTalkChannelListener:
    """DingTalk Bot consumer that processes pending dingtalk_inbox rows asynchronously.

    Producer: FastAPI callback endpoint stores messages into dingtalk_inbox.
    Consumer: This listener queries pending rows and processes them via dispatcher.
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

    async def start(self) -> None:
        if self._consumer_task is None or self._consumer_task.done():
            self._consumer_task = asyncio.create_task(self._consumer_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._consumer_task:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass

    async def _load_active_dingtalk_platforms(self) -> list[_PlatformEntry]:
        """Load all active DingTalk Bot platforms."""
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(Platform.id, Platform.project_id, Platform.api_key, Platform.config)
                    .where(Platform.is_active.is_(True), Platform.type == "dingtalk_bot")
                )
            ).all()
        platforms: list[_PlatformEntry] = []
        for pid, project_id, api_key, cfg_dict in rows:
            try:
                cfg = DingTalkPlatformConfig(**(cfg_dict or {}))
                platforms.append(_PlatformEntry(
                    id=pid,
                    project_id=project_id,
                    api_key=api_key,
                    cfg=cfg,
                ))
            except Exception as e:
                print(f"[DINGTALK] Skip platform {pid}: invalid config: {e}")
        return platforms

    async def _consumer_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                platforms = await self._load_active_dingtalk_platforms()
                for p in platforms:
                    try:
                        await self._process_pending_for_platform(p)
                    except Exception as e:
                        print(f"[DINGTALK] Consumer error for platform {p.id}: {e}")
                # Sleep using first platform's interval or default
                interval = platforms[0].cfg.consumer_poll_interval_seconds if platforms else 5
                await asyncio.sleep(max(1, int(interval)))
            except Exception as e:
                print(f"[DINGTALK] Consumer supervisor error: {e}")
                await asyncio.sleep(5)

    async def _select_candidates(
        self,
        session: AsyncSession,
        platform: _PlatformEntry,
        batch_size: int,
        max_retries: int,
    ) -> list[DingTalkInbox]:
        """Select a batch of candidate records to process for the given platform."""
        # Pending first
        pending = (
            await session.execute(
                select(DingTalkInbox)
                .where(DingTalkInbox.platform_id == platform.id, DingTalkInbox.status == "pending")
                .order_by(DingTalkInbox.fetched_at.asc())
                .with_for_update(skip_locked=True)
                .limit(batch_size)
            )
        ).scalars().all()

        remaining = batch_size - len(pending)
        candidates: list[DingTalkInbox] = list(pending)

        if remaining > 0:
            failed = (
                await session.execute(
                    select(DingTalkInbox)
                    .where(
                        DingTalkInbox.platform_id == platform.id,
                        DingTalkInbox.status == "failed",
                        DingTalkInbox.retry_count < max_retries,
                    )
                    .order_by(DingTalkInbox.processed_at.asc().nullsfirst())
                    .with_for_update(skip_locked=True)
                    .limit(batch_size * 3)
                )
            ).scalars().all()
            now = datetime.now(timezone.utc)
            for record in failed:
                delay = max(1, 2 ** int(record.retry_count or 0))
                if not record.processed_at or (now - record.processed_at).total_seconds() >= delay:
                    candidates.append(record)
                    if len(candidates) >= batch_size:
                        break

        return candidates

    async def _claim_record(self, session: AsyncSession, record: DingTalkInbox) -> bool:
        """Attempt to mark a record as processing. Returns True if claimed successfully."""
        try:
            record.status = "processing"
            record.error_message = None
            await session.commit()
            return True
        except Exception as e:
            print(f"[DINGTALK] Claiming record failed (skip): {e}")
            await session.rollback()
            return False

    def _build_mapped_message(self, platform: _PlatformEntry, record: DingTalkInbox) -> dict[str, Any]:
        """Build the NormalizedMessage-like raw dict for downstream normalization."""
        raw_payload = record.raw_payload or {}

        # Build DingTalk-specific context used by adapter selection/sending
        dingtalk_ctx: dict[str, Any] = {
            "session_webhook": record.session_webhook or raw_payload.get("session_webhook") or "",
            "conversation_id": record.conversation_id or "",
            "conversation_type": record.conversation_type or "1",
            "sender_nick": record.sender_nick or "",
            "msg_type": record.msg_type,
        }

        return {
            "source": "dingtalk",
            "from_uid": record.from_user,
            "content": record.content or "",
            "platform_api_key": platform.api_key or "",
            "platform_type": "dingtalk_bot",
            "platform_id": str(platform.id),
            "extra": {
                "project_id": str(platform.project_id),
                "message_id": record.message_id,
                "msg_type": record.msg_type,
                "dingtalk": dingtalk_ctx,
            },
        }

    async def _get_or_register_visitor(
        self,
        platform: _PlatformEntry,
        record: DingTalkInbox,
    ) -> tuple[Any | None, str | None, str | None]:
        """Visitor retrieval/registration with cache-first approach."""
        display_name: str | None = record.sender_nick or record.from_user
        avatar_url: str | None = None
        visitor = None

        # Check cache first
        try:
            cache_key = self._visitor_service.make_cache_key(str(platform.project_id), "dingtalk_bot", record.from_user)
            cached = await self._visitor_service.get_cached(cache_key)
            if cached:
                display_name = cached.nickname or cached.name or display_name
                avatar_url = cached.avatar_url
                return cached, display_name, avatar_url
        except Exception as e:
            print(f"[DINGTALK] Visitor cache lookup failed for {platform.id}: {e}")

        # Register or get visitor via tgo-api
        if platform.api_key:
            try:
                visitor = await self._visitor_service.register_or_get(
                    platform_api_key=platform.api_key,
                    project_id=str(platform.project_id),
                    platform_type="dingtalk_bot",
                    platform_open_id=record.from_user,
                    nickname=display_name,
                    avatar_url=avatar_url,
                )
            except Exception as e:
                print(f"[DINGTALK] Visitor registration failed for {platform.id}: {e}")

        return visitor, display_name, avatar_url

    def _attach_profile_to_extra(self, mapped_raw: dict[str, Any], display_name: str | None, avatar_url: str | None) -> None:
        """Attach visitor profile fields into mapped_raw.extra.visitor_profile safely."""
        try:
            extra = mapped_raw.get("extra") or {}
            extra["visitor_profile"] = {"nickname": display_name, "avatar_url": avatar_url}
            mapped_raw["extra"] = extra
        except Exception:
            pass

    async def _finalize_success(self, session: AsyncSession, record: DingTalkInbox, reply_text: str | None) -> None:
        """Mark record as completed with optional reply text."""
        record.ai_reply = reply_text
        finalize_inbox_success(record)
        try:
            await session.commit()
        except Exception as e2:
            print(f"[DINGTALK] Commit completed status failed (ignore): {e2}")
            await session.rollback()

    async def _finalize_failure(
        self,
        session: AsyncSession,
        platform: _PlatformEntry,
        record: DingTalkInbox,
        error: Exception,
        max_retries: int,
    ) -> None:
        """Mark record as failed with retry increment and error message."""
        print(f"[DINGTALK] Processing failed for {platform.id}: {error}")
        finalize_inbox_failure(
            record,
            error,
            max_retry_attempts=max_retries,
        )
        try:
            await session.commit()
        except Exception as e2:
            print(f"[DINGTALK] Commit failed status failed (ignore): {e2}")
            await session.rollback()

    async def _process_pending_for_platform(self, p: _PlatformEntry) -> None:
        batch_size = max(1, int(getattr(p.cfg, "processing_batch_size", 10) or 10))
        max_retries = max(0, int(getattr(p.cfg, "max_retry_attempts", 3) or 3))

        async with self._session_factory() as db:
            # Select candidate records for this platform (pending + eligible failed)
            candidates = await claim_inbox_batch(
                db,
                DingTalkInbox,
                platform_id=p.id,
                batch_size=batch_size,
                max_retry_attempts=max_retries,
            )
            if not candidates:
                return

            for rec in candidates:
                try:
                    # Build mapped message
                    mapped_raw: dict[str, Any] = self._build_mapped_message(p, rec)

                    # Visitor retrieval/registration with cache-first approach
                    visitor, display_name, avatar_url = await self._get_or_register_visitor(p, rec)
                    self._attach_profile_to_extra(mapped_raw, display_name, avatar_url)

                    # Normalize and process
                    msg: NormalizedMessage = await self._normalizer.normalize(mapped_raw)
                    reply_text = await process_message(
                        msg=msg,
                        db=db,
                        tgo_api_client=self._tgo_api_client,
                        sse_manager=self._sse_manager,
                    )

                    # Finalize success
                    await self._finalize_success(db, rec, reply_text)
                except Exception as e:
                    # Finalize failure with retry increment
                    await self._finalize_failure(db, p, rec, e, max_retries)

