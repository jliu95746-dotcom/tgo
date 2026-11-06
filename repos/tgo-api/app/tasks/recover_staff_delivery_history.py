"""Recover staff history and accepted handoffs independently of browsers."""

import asyncio
from uuid import UUID

from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.services.staff_delivery import (
    recover_pending_history,
    recover_stale_external_deliveries,
)
from app.services.staff_delivery_training import recover_pending_training
from app.services.session_command_delivery import recover_session_commands
from app.services.session_command_membership import recover_channel_effects
from app.services.handoff_dispatch_recovery import recover_pending_handoffs

logger = get_logger(__name__)
_task: asyncio.Task[None] | None = None


async def _recover_loop() -> None:
    handoff_cursor: UUID | None = None
    while True:
        for phase_name, recover in (
            ("external_unknown", recover_stale_external_deliveries),
            ("history", recover_pending_history),
            ("training", recover_pending_training),
            ("session_notice", recover_session_commands),
            ("channel_effects", recover_channel_effects),
        ):
            try:
                with SessionLocal() as db:
                    await recover(db)
            except Exception as exc:
                logger.error(
                    "Staff recovery phase %s failed: %s",
                    phase_name, type(exc).__name__,
                )
        try:
            with SessionLocal() as db:
                handoffs = await recover_pending_handoffs(
                    db, after_request_id=handoff_cursor,
                )
                handoff_cursor = handoffs.next_cursor
        except Exception as exc:
            logger.error(
                "Handoff recovery sweep failed: %s", type(exc).__name__,
            )
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
