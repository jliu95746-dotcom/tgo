"""Report only after all owned model and tool work has ended."""

import asyncio
import logging

from anyio import CancelScope

from app.schemas.reply_phase import ReplyPhaseIdentity
from app.services.api_service import api_service_client

logger = logging.getLogger(__name__)


async def report_phase_ended(phase: ReplyPhaseIdentity | None) -> None:
    if phase is None:
        return
    with CancelScope(shield=True):
        try:
            async with asyncio.timeout(4):
                await api_service_client.confirm_reply_phase_ended(phase)
        except Exception as exc:
            # Do not log phase capabilities or replace the original result.
            logger.warning("AI phase receipt failed: %s", type(exc).__name__)
