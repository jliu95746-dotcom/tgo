"""Independent source-event recovery loop; no browser or notice dependency."""

import asyncio

from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.services.workflow_source_dispatch import (
    claim_source_event,
    collect_source_events,
    deliver_source_event,
)

logger = get_logger(__name__)
_task: asyncio.Task[None] | None = None


async def _dispatch_loop() -> None:
    while True:
        try:
            with SessionLocal() as db:
                collect_source_events(db)
            for _ in range(10):
                with SessionLocal() as db:
                    claim = claim_source_event(db)
                    if claim is None:
                        break
                    await deliver_source_event(db, claim)
        except Exception as exc:
            logger.error(
                "Workflow source recovery failed: %s", type(exc).__name__
            )
        await asyncio.sleep(10)


def start_workflow_source_dispatch() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_dispatch_loop())


async def stop_workflow_source_dispatch() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
