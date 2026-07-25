from __future__ import annotations

import asyncio
import base64
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.db.models import Platform, WuKongIMInbox
from app.domain.entities import NormalizedMessage
from app.domain.ports import MessageNormalizer, TgoApiClient, SSEManager
from app.domain.services.dispatcher import process_message
from app.domain.services.inbox_reliability import (
    claim_inbox_batch,
    finalize_inbox_failure,
    finalize_inbox_success,
)
from app.infra.visitor_client import VisitorService


class WuKongIMPlatformConfig(BaseModel):
    """Per-platform WuKongIM config stored in Platform.config when type='website'.
    Only consumer settings are used here; webhook producer stores rows.
    """

    processing_batch_size: int = 10
    max_retry_attempts: int = 3
    consumer_poll_interval_seconds: int = 5


@dataclass
class _PlatformEntry:
    id: uuid.UUID
    project_id: uuid.UUID
    api_key: str | None
    cfg: WuKongIMPlatformConfig


class WuKongIMChannelListener:
    """Consumer that processes pending wukongim_inbox rows asynchronously.

    Producer: FastAPI callback endpoint stores messages into wukongim_inbox.
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

    async def _load_active_platforms(self) -> list[_PlatformEntry]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(Platform.id, Platform.project_id, Platform.api_key, Platform.config)
                    .where(Platform.is_active.is_(True), Platform.type == "website")
                )
            ).all()
        platforms: list[_PlatformEntry] = []
        for pid, project_id, api_key, cfg_dict in rows:
            try:
                cfg = WuKongIMPlatformConfig(**(cfg_dict or {}))
                platforms.append(_PlatformEntry(id=pid, project_id=project_id, api_key=api_key, cfg=cfg))
            except Exception as e:
                print(f"[WUKONGIM] Skip platform {pid}: invalid config: {e}")
        return platforms

    async def _consumer_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                platforms = await self._load_active_platforms()
                for p in platforms:
                    try:
                        await self._process_pending_for_platform(p)
                    except Exception as e:
                        print(f"[WUKONGIM] Consumer error for platform {p.id}: {e}")
                interval = platforms[0].cfg.consumer_poll_interval_seconds if platforms else 5
                await asyncio.sleep(max(1, int(interval)))
            except Exception as e:
                print(f"[WUKONGIM] Consumer supervisor error: {e}")
                await asyncio.sleep(5)

    async def _process_pending_for_platform(self, p: _PlatformEntry) -> None:
        batch_size = max(1, int(getattr(p.cfg, "processing_batch_size", 10) or 10))
        max_retries = max(0, int(getattr(p.cfg, "max_retry_attempts", 3) or 3))

        async with self._session_factory() as db:
            candidates = await claim_inbox_batch(
                db,
                WuKongIMInbox,
                platform_id=p.id,
                batch_size=batch_size,
                max_retry_attempts=max_retries,
            )

            if not candidates:
                return

            for rec in candidates:
                try:
                    # Use decoded plain content; backward-compat: detect and decode base64 if necessary
                    content = rec.payload or ""
                    if content:
                        try:
                            decoded_bytes = base64.b64decode(content, validate=True)
                            reenc = base64.b64encode(decoded_bytes).decode().rstrip("=")
                            if reenc == (content.strip().rstrip("=")):
                                content = decoded_bytes.decode("utf-8", errors="replace")
                        except Exception:
                            pass

                    # Map to NormalizedMessage (put channel info into extra)
                    mapped_raw: dict[str, Any] = {
                        "source": "wukongim",
                        "from_uid": rec.from_uid,
                        "content": content,
                        "platform_api_key": p.api_key or "",
                        "platform_type": "website",
                        "platform_id": str(p.id),
                        "extra": {
                            "project_id": str(p.project_id),
                            "channel_id": rec.channel_id,
                            "channel_type": rec.channel_type,
                            "message_seq": rec.message_seq,
                            "timestamp": rec.timestamp,
                            "client_msg_no": rec.client_msg_no,
                            "message_id": rec.message_id,
                        },
                    }

                    # WuKongIM: no visitor registration; from_uid is the visitor id

                    msg: NormalizedMessage = await self._normalizer.normalize(mapped_raw)
                    reply_text = await process_message(
                        msg=msg,
                        db=db,
                        tgo_api_client=self._tgo_api_client,
                        sse_manager=self._sse_manager,
                    )

                    rec.ai_reply = reply_text
                    finalize_inbox_success(rec)
                    await db.commit()
                except Exception as e:
                    print(f"[WUKONGIM] Processing failed for {p.id}: {e}")
                    finalize_inbox_failure(
                        rec,
                        e,
                        max_retry_attempts=max_retries,
                    )
                    await db.commit()

