"""Isolated asynchronous version preparation."""
import asyncio
from uuid import UUID

from ..database import reset_db_state
from ..services.knowledge_version_prepare import process_revision
from .celery_app import celery_app
from .website_crawling import create_crawl_event_loop


@celery_app.task(name='prepare_knowledge_version')
def prepare_knowledge_version(project: str, version: str, attempt: str) -> str:
    reset_db_state()
    loop = create_crawl_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(process_revision(UUID(project), UUID(version), UUID(attempt)))
    finally:
        reset_db_state()
        loop.close()
