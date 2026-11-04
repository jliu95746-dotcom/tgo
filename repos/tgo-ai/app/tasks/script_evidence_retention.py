"""Periodic local maintenance; it never executes a business script."""

import asyncio
from collections.abc import Awaitable, Callable
import logging

from app.config import settings
from app.database import AsyncSessionLocal
from app.services.script_evidence_retention import purge_script_evidence

logger = logging.getLogger(__name__)
Cleanup = Callable[[], Awaitable[int]]


async def cleanup_once() -> int:
    async with AsyncSessionLocal() as db:
        return await purge_script_evidence(db)


async def run_retention(
    stop: asyncio.Event,
    *,
    cleanup: Cleanup = cleanup_once,
    interval_seconds: float | None = None,
    drain_seconds: float = 1,
    cleanup_timeout: float = 10,
) -> None:
    interval = (
        settings.script_trial_cleanup_interval_seconds
        if interval_seconds is None
        else interval_seconds
    )
    if min(interval, drain_seconds, cleanup_timeout) <= 0:
        raise ValueError("Retention maintenance bounds must be positive")
    while not stop.is_set():
        count = 0
        try:
            async with asyncio.timeout(cleanup_timeout):
                count = await cleanup()
            if count:
                logger.info(
                    "Cleared expired script evidence bodies: %s", count
                )
        except Exception as error:
            logger.warning(
                "Script evidence cleanup unavailable: %s", type(error).__name__
            )
        if stop.is_set():
            break
        delay = drain_seconds if count else interval
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except TimeoutError:
            pass
