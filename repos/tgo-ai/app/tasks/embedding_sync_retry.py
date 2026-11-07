"""Periodic retry task for embedding config synchronization.

This scheduler finds ProjectAIConfig records that are failed/not_synced or
stale pending and re-dispatches them to the RAG service using the existing
fire_and_forget_embedding_sync() mechanism.

It is a lightweight asyncio loop gated by configuration flags and interval
settings in app.config.Settings. It is disabled by default and can be enabled
by setting EMBEDDING_SYNC_RETRY_ENABLED=true.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import List

from sqlalchemy import and_, or_, select

from app.config import settings
from app.core.logging import get_logger
from app.database import AsyncSessionLocal
from app.models.project_ai_config import ProjectAIConfig
from app.services.rag_embedding_sync_service import (
    build_embedding_configs,
    fire_and_forget_embedding_sync,
)

logger = get_logger("tasks.embedding_sync_retry")

# In-process lock to avoid overlapping runs
_run_lock = asyncio.Lock()


async def _collect_retry_candidates() -> List[ProjectAIConfig]:
    """Collect rows that should be retried based on status/age/attempt cap."""
    cutoff = datetime.now(timezone.utc) - timedelta(
        minutes=settings.embedding_sync_retry_stale_pending_minutes
    )
    max_attempts = settings.embedding_sync_retry_max_attempts

    async with AsyncSessionLocal() as session:
        stmt = select(ProjectAIConfig).where(
            and_(
                or_(
                    ProjectAIConfig.sync_status == "failed",
                    ProjectAIConfig.sync_status == "not_synced",
                    and_(
                        ProjectAIConfig.sync_status == "pending",
                        ProjectAIConfig.updated_at < cutoff,
                    ),
                ),
                or_(
                    ProjectAIConfig.sync_attempt_count.is_(None),
                    ProjectAIConfig.sync_attempt_count < max_attempts,
                ),
            )
        )
        res = await session.execute(stmt)
        rows: List[ProjectAIConfig] = list(res.scalars().all())
        return rows


async def run_retry_once() -> int:
    """Run a single retry pass. Returns number of configs dispatched."""
    async with _run_lock:
        rows = await _collect_retry_candidates()
        if not rows:
            logger.info("embedding-sync-retry: no candidates found")
            return 0
        print("run_retry_once....", rows)
        async with AsyncSessionLocal() as session:
            configs = await build_embedding_configs(session, rows)
        if not configs:
            logger.info(
                (
                    'embedding-sync-retry: candidates had no valid embedding '
                    'configs'
                ),
                count=len(rows),
            )
            return 0

        fire_and_forget_embedding_sync(configs)
        logger.info(
            "embedding-sync-retry: dispatched configs",
            candidates=len(rows),
            dispatched=len(configs),
        )
        return len(configs)


async def start_embedding_sync_retry_loop(stop_event: asyncio.Event) -> None:
    """Start an asyncio loop that runs retry passes periodically.

    The loop waits for an initial interval window, then performs a pass,
    and then sleeps for the configured interval again. It exits when
    stop_event is set.
    """
    print("start_embedding_sync_retry_loop....")
    if not settings.embedding_sync_retry_enabled:
        logger.info(
            "embedding-sync-retry: disabled via settings; loop not started"
        )
        return

    interval = max(30, int(settings.embedding_sync_retry_interval_seconds))
    logger.info(
        "embedding-sync-retry: starting loop",
        interval_seconds=interval,
        max_attempts=settings.embedding_sync_retry_max_attempts,
        stale_pending_minutes=(
            settings.embedding_sync_retry_stale_pending_minutes
        ),
    )

    # Wait one interval before first pass to avoid interfering with
    # startup/tests
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=interval)
    except asyncio.TimeoutError:
        pass

    while not stop_event.is_set():
        try:
            await run_retry_once()
        except Exception as e:  # pragma: no cover - defensive
            logger.error("embedding-sync-retry: pass failed", error=str(e))
        # Sleep until next interval or stop
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue
