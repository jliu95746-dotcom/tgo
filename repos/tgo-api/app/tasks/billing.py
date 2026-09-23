"""Wake durable billing work while retaining callback processing during sales pauses."""

import asyncio
import time

from app.core.config import settings
from app.core.logging import get_logger
from app.services.billing_jobs import run_one
from app.services.billing_maintenance import maintain_subscriptions, reconcile_one
from app.services.ai_usage_recovery import reconcile_deliveries
from app.services.billing_reconciliation import schedule_statements

logger = get_logger(__name__)
_task: asyncio.Task[None] | None = None


async def _run() -> None:
    last_maintenance = 0.0
    while True:
        try:
            await asyncio.to_thread(run_one)
            if time.monotonic() - last_maintenance >= 60:
                await asyncio.to_thread(maintain_subscriptions)
                await reconcile_deliveries()
                if settings.WECHAT_PAY_MCH_ID:
                    await asyncio.to_thread(schedule_statements)
                last_maintenance = time.monotonic()
            if settings.WECHAT_PAY_MCH_ID:
                await asyncio.to_thread(reconcile_one)
        except Exception as exc:
            logger.error("Billing worker failed: %s", type(exc).__name__)
        await asyncio.sleep(2)


def start_billing() -> None:
    global _task
    if (
        settings.SAAS_ENABLED
        and settings.SAAS_BILLING_ENABLED
        and (_task is None or _task.done())
    ):
        _task = asyncio.create_task(_run())


async def stop_billing() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
