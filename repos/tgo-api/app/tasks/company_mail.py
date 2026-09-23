"""Wake durable outbox processing; restart safety resides in database leases."""

import asyncio

from app.core.config import settings
from app.core.logging import get_logger
from app.services.company_mail_delivery import deliver_one

logger = get_logger(__name__)
_task: asyncio.Task[None] | None = None


async def _run() -> None:
    while True:
        try:
            delivered = await asyncio.to_thread(deliver_one)
        except Exception as exc:
            logger.error("Company mail worker failed: %s", type(exc).__name__)
            delivered = False
        await asyncio.sleep(0.2 if delivered else 5)


def start_company_mail() -> None:
    global _task
    if settings.SAAS_ENABLED and settings.SAAS_SMTP_HOST:
        if _task is None or _task.done():
            _task = asyncio.create_task(_run())


async def stop_company_mail() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
