"""Recover accepted staff messages' IM history independently of browser state."""

import asyncio

from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.services.staff_delivery import recover_pending_history
from app.services.staff_delivery_training import recover_pending_training

logger = get_logger(__name__)
_task: asyncio.Task[None] | None = None


async def _recover_loop() -> None:
    while True:
        try:
            with SessionLocal() as db:
                await recover_pending_history(db)
                await recover_pending_training(db)
        except Exception as exc:
            logger.error("Staff history recovery failed: %s", type(exc).__name__)
        await asyncio.sleep(5)


def start_staff_history_recovery() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_recover_loop())


async def stop_staff_history_recovery() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
