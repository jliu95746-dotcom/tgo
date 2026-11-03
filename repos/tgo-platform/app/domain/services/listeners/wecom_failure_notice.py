"""Persist and send one fallback notice for failed WeCom KF media messages."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.db.models import WeComInbox
from app.domain.services.adapters.wecom import WeComAdapter
from app.domain.services.wecom_credentials import resolve_wecom_kf_secret


logger = logging.getLogger(__name__)

FALLBACK_TEXT = (
    "抱歉，这条媒体消息暂时无法处理。请重新发送，或用文字描述您的问题；"
    "如需人工服务，请回复“转人工”。"
)


class _WeComConfig(Protocol):
    corp_id: str
    kf_secret: str
    app_secret: str
    processing_batch_size: int


class _Platform(Protocol):
    id: UUID
    platform_type: str
    cfg: _WeComConfig


class WeComFailureNotifier:
    """Single-attempt sender; ambiguous outcomes stay visible as ``unknown``.

    ``sending`` is committed before the external call. A crashed attempt becomes
    ``unknown`` after its lease, so restart cannot silently duplicate a reply.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def _claim(self, platform: _Platform) -> WeComInbox | None:
        now = datetime.now(timezone.utc)
        stale_before = now - timedelta(
            seconds=max(300, settings.request_timeout_seconds + 60)
        )
        async with self._session_factory() as session:
            record = await session.scalar(
                select(WeComInbox)
                .where(
                    WeComInbox.platform_id == platform.id,
                    WeComInbox.source_type == "wecom_kf",
                    WeComInbox.is_from_colleague.is_(False),
                    WeComInbox.status.in_(["media_failed", "unsupported_media"]),
                    or_(
                        WeComInbox.failure_notice_status.is_(None),
                        and_(
                            WeComInbox.failure_notice_status == "sending",
                            or_(
                                WeComInbox.failure_notice_started_at.is_(None),
                                WeComInbox.failure_notice_started_at <= stale_before,
                            ),
                        ),
                    ),
                )
                .order_by(WeComInbox.fetched_at.asc())
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if record is None:
                return None
            if record.failure_notice_status == "sending":
                record.failure_notice_status = "unknown"
                record.failure_notice_finished_at = now
                record.failure_notice_error = "interrupted send; outcome unknown"
                await session.commit()
                logger.error(
                    "[WECOM] Media fallback send interrupted for inbox_id=%s", record.id
                )
                return None
            record.failure_notice_status = "sending"
            record.failure_notice_started_at = now
            record.failure_notice_error = None
            await session.commit()
            return record

    async def _finish(
        self, record: WeComInbox, status: str, error: str | None = None
    ) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(WeComInbox)
                .where(
                    WeComInbox.id == record.id,
                    WeComInbox.failure_notice_status == "sending",
                )
                .values(
                    failure_notice_status=status,
                    failure_notice_finished_at=datetime.now(timezone.utc),
                    failure_notice_error=error,
                )
            )
            await session.commit()

    async def process_platform(self, platform: _Platform) -> None:
        if platform.platform_type != "wecom":
            return
        for _ in range(min(100, max(1, int(platform.cfg.processing_batch_size)))):
            record = await self._claim(platform)
            if record is None:
                return
            await self._send_claimed_record(platform, record)

    async def _send_claimed_record(
        self, platform: _Platform, record: WeComInbox
    ) -> None:
        secret = resolve_wecom_kf_secret(
            platform.cfg.kf_secret, platform.cfg.app_secret
        )
        if not (
            platform.cfg.corp_id and secret and record.from_user and record.open_kfid
        ):
            await self._finish(
                record, "needs_attention", "missing recipient or credentials"
            )
            logger.error(
                "[WECOM] Media fallback needs attention for inbox_id=%s", record.id
            )
            return
        adapter = WeComAdapter(
            corp_id=platform.cfg.corp_id,
            agent_id=None,
            app_secret=secret,
            to_user=record.from_user,
            is_from_colleague=False,
            open_kfid=record.open_kfid,
            external_userid=record.from_user,
            source_message_id=f"media-fallback:{record.platform_id}:{record.message_id}",
        )
        try:
            await adapter.send_final({"text": FALLBACK_TEXT})
        except Exception as exc:
            # A timeout or server error may follow a successful provider send.
            # Keep the row for operator reconciliation; do not automatically retry.
            await self._finish(record, "unknown", type(exc).__name__)
            logger.exception(
                "[WECOM] Media fallback outcome unknown for inbox_id=%s", record.id
            )
        else:
            await self._finish(record, "submitted")
