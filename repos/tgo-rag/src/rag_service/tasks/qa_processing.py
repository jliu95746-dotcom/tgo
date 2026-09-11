"""
QA pair processing tasks.

This module provides Celery tasks for processing QA pairs,
generating embeddings, and storing them in the vector store.

Unlike file processing, QA pairs are NOT chunked - each Q+A
is treated as a single document for embedding.
"""

import asyncio
from typing import Any, Dict, List, TypedDict
from celery import Task
from uuid import UUID

from .celery_app import celery_app
from ..database import get_db_session, reset_db_state
from ..logging_config import get_logger
from ..services.qa_documents import QAProcessingResult, process_qa_pair_async
from ..services.qa_errors import safe_qa_failure

logger = get_logger(__name__)


class QABatchResult(TypedDict, total=False):
    success: bool
    processed_count: int
    failed_count: int
    results: List[QAProcessingResult]
    error: str


async def process_qa_pairs_batch_async(
    qa_pair_ids: List[UUID],
    project_id: UUID,
) -> QABatchResult:
    """
    Process multiple QA pairs in batch.

    Args:
        qa_pair_ids: List of QA pair UUIDs to process
        project_id: Project ID for embedding service resolution

    Returns:
        Dict with batch processing results
    """
    results: QABatchResult = {
        "success": True,
        "processed_count": 0,
        "failed_count": 0,
        "results": [],
    }

    for qa_pair_id in qa_pair_ids:
        result = await process_qa_pair_async(qa_pair_id, project_id)
        results["results"].append(result)

        if result["success"]:
            results["processed_count"] += 1
        else:
            results["failed_count"] += 1

    results["success"] = results["failed_count"] == 0
    return results


async def delete_qa_pair_document_async(
    qa_pair_id: UUID,
    document_id: UUID,
    project_id: UUID,
) -> Dict[str, Any]:
    """
    Delete the FileDocument and vector embedding for a QA pair.

    Args:
        qa_pair_id: UUID of the QA pair
        document_id: UUID of the associated FileDocument
        project_id: Project ID

    Returns:
        Dict with deletion result
    """
    from ..services.qa_documents import current_pair, remove_qa_document

    async with get_db_session() as db:
        pair = await current_pair(db, qa_pair_id, project_id)
        if pair is None or pair.document_id != document_id:
            return {"success": False, "error": "QA document not found for project"}
        await remove_qa_document(db, pair)
        await db.commit()
    return {"success": True, "document_id": str(document_id)}


# ============== Celery Tasks ==============

@celery_app.task(bind=True, name="process_qa_pair_task")
def process_qa_pair_task(
    self: Task, qa_pair_id: str, project_id: str, is_update: bool = False
) -> QAProcessingResult:
    """
    Celery task for processing a single QA pair.

    Args:
        qa_pair_id: UUID string of the QA pair
        project_id: UUID string of the project
        is_update: Whether this is an update operation

    Returns:
        Processing result dictionary
    """
    try:
        qa_pair_uuid = UUID(qa_pair_id)
        project_uuid = UUID(project_id)

        reset_db_state()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            result = loop.run_until_complete(
                process_qa_pair_async(qa_pair_uuid, project_uuid, is_update)
            )
            return result
        finally:
            # Clean up database connections before closing the loop
            reset_db_state()
            loop.close()

    except Exception as e:
        logger.error("QA pair processing task failed", error_type=type(e).__name__)
        return {
            "success": False,
            "qa_pair_id": qa_pair_id,
            "error": safe_qa_failure(e),
        }


@celery_app.task(bind=True, name="process_qa_pairs_batch_task")
def process_qa_pairs_batch_task(
    self: Task, qa_pair_ids: List[str], project_id: str
) -> QABatchResult:
    """
    Celery task for processing multiple QA pairs in batch.

    Args:
        qa_pair_ids: List of UUID strings
        project_id: UUID string of the project

    Returns:
        Batch processing result dictionary
    """
    try:
        qa_pair_uuids = [UUID(qid) for qid in qa_pair_ids]
        project_uuid = UUID(project_id)

        reset_db_state()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            result = loop.run_until_complete(
                process_qa_pairs_batch_async(qa_pair_uuids, project_uuid)
            )
            return result
        finally:
            # Clean up database connections before closing the loop
            reset_db_state()
            loop.close()

    except Exception as e:
        logger.error(
            "QA pairs batch processing task failed", error_type=type(e).__name__,
        )
        return {
            "success": False,
            "error": safe_qa_failure(e),
        }
