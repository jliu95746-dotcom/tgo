"""Periodically clean generated visitor avatar files with no database owner."""

import asyncio

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.services.visitor_avatar_retention import (
    AvatarCleanupResult,
    cleanup_orphan_visitor_avatars,
)

logger = get_logger(__name__)
_task: asyncio.Task[None] | None = None
_BATCH_SIZE = 100


def _clean_one_batch() -> AvatarCleanupResult:
    with SessionLocal() as db:
        return cleanup_orphan_visitor_avatars(
            db,
            older_than_seconds=(
                settings.VISITOR_AVATAR_CLEANUP_AGE_DAYS * 24 * 60 * 60
            ),
            limit=_BATCH_SIZE,
        )


async def _run_periodically() -> None:
    while True:
        try:
            result = await asyncio.to_thread(_clean_one_batch)
            if result.deleted or result.errors:
                logger.info(
                    "Visitor avatar cleanup: deleted=%s errors=%s",
                    result.deleted, result.errors,
                )
        except Exception as exc:
            logger.error(
                "Visitor avatar cleanup failed: %s", type(exc).__name__
            )
            result = AvatarCleanupResult(errors=1)
        await asyncio.sleep(
            1 if result.deleted >= _BATCH_SIZE
            else settings.VISITOR_AVATAR_CLEANUP_INTERVAL_SECONDS
        )


def start_visitor_avatar_retention() -> None:
    global _task
    if (
        settings.VISITOR_AVATAR_CLEANUP_ENABLED
        and (_task is None or _task.done())
    ):
        _task = asyncio.create_task(_run_periodically())


async def stop_visitor_avatar_retention() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
