"""Periodically revoke waiting events without opening retained business input."""

import asyncio

from app.core.logging import logger
from app.integrations.http_client import HttpClient
from app.services.event_source_audit import sweep_waiting_event_sources
from celery_app.celery import celery_app


@celery_app.task(
    name="audit_waiting_event_sources",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def audit_waiting_event_sources_task(self) -> int:
    try:
        return asyncio.run(_audit_with_http_cleanup())
    except Exception as error:
        logger.error("Waiting event source audit failed: %s", type(error).__name__)
        raise self.retry(exc=RuntimeError("事件来源巡检暂未完成，请检查后台服务。")) from None


async def _audit_with_http_cleanup() -> int:
    try:
        return await sweep_waiting_event_sources()
    finally:
        # The next task runs in a new asyncio.run loop in this same Worker.
        # No pooled socket may survive the loop that created it.
        await HttpClient.close_client()
