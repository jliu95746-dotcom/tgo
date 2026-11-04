"""Periodically redact expired assist draft bodies in bounded batches."""

import asyncio

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.services.assist_draft_retention import redact_expired_assist_drafts
from app.services.hidden_shadow_retention import (
    redact_expired_hidden_shadow_runs,
)

logger = get_logger(__name__)
_task: asyncio.Task[None] | None = None
_BATCH_SIZE = 100


def _redact_one_batch() -> int:
    with SessionLocal() as db:
        drafts = redact_expired_assist_drafts(
            db, days=settings.ASSIST_DRAFT_RETENTION_DAYS,
            limit=_BATCH_SIZE,
        )
        hidden = redact_expired_hidden_shadow_runs(
            db, days=settings.ASSIST_DRAFT_RETENTION_DAYS,
            limit=_BATCH_SIZE,
        )
        return drafts + hidden


async def _run_periodically() -> None:
    while True:
        try:
            redacted = await asyncio.to_thread(_redact_one_batch)
            if redacted:
                logger.info(
                    "Assist draft retention redacted %s records", redacted
                )
        except Exception as exc:
            logger.error(
                "Assist draft retention failed: %s", type(exc).__name__
            )
            redacted = 0
        await asyncio.sleep(
            1 if redacted >= _BATCH_SIZE
            else settings.ASSIST_DRAFT_RETENTION_INTERVAL_SECONDS
        )


def start_assist_draft_retention() -> None:
    global _task
    if (
        settings.ASSIST_DRAFT_RETENTION_ENABLED
        and (_task is None or _task.done())
    ):
        _task = asyncio.create_task(_run_periodically())


async def stop_assist_draft_retention() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
