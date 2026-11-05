"""Bounded cleanup runs with the Workflow API, independently of Celery beat."""
import asyncio

from app.core.logging import logger
from app.database import AsyncSessionLocal
from app.services.workflow_ai_results import purge


async def reap_forever() -> None:
    while True:
        try:
            async with AsyncSessionLocal() as db:
                await purge(db)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "Workflow AI result cleanup deferred: %s",
                type(error).__name__,
            )
        await asyncio.sleep(60)
