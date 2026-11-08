"""Isolated asynchronous version preparation."""
import asyncio
from uuid import UUID

from ..config import get_settings
from ..database import close_database, get_db_session, reset_db_state
from ..logging_config import get_logger
from ..services.knowledge_version_artifact_cleanup import (
    ArtifactCleanupResult,
    collect_artifacts,
)
from ..services.knowledge_version_prepare import process_revision
from ..services.qa_errors import safe_exception_chain
from .celery_app import celery_app
from .website_crawling import create_crawl_event_loop

logger = get_logger(__name__)


async def _collect_artifacts() -> ArtifactCleanupResult:
    async with get_db_session() as db:
        return await collect_artifacts(
            db, get_settings().knowledge_version_cleanup_batch_size
        )


@celery_app.task(
    name="cleanup_knowledge_version_artifacts",
    autoretry_for=(RuntimeError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def cleanup_knowledge_version_artifacts() -> ArtifactCleanupResult:
    reset_db_state()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_collect_artifacts())
    except Exception as error:
        logger.warning(
            "Knowledge version artifact cleanup retry required",
            error_type=safe_exception_chain(error),
        )
        raise RuntimeError("知识版本文件清理暂未完成，已保留待重试记录。") from None
    finally:
        try:
            loop.run_until_complete(close_database())
        except Exception as error:
            logger.warning(
                "Knowledge version cleanup database disposal deferred",
                error_type=safe_exception_chain(error),
            )
        reset_db_state()
        loop.close()


@celery_app.task(name="prepare_knowledge_version")
def prepare_knowledge_version(project: str, version: str, attempt: str) -> str:
    reset_db_state()
    loop = create_crawl_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(
            process_revision(UUID(project), UUID(version), UUID(attempt))
        )
    finally:
        reset_db_state()
        loop.close()
