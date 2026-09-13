"""
QA (Question-Answer) pair management endpoints.

This module provides API endpoints for managing QA knowledge bases,
including creating, updating, deleting, and listing QA pairs.
"""

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db_session_dependency
from ..logging_config import get_logger
from ..models import Collection, CollectionType, QAPair
from ..schemas.common import ErrorResponse
from ..services.qa_errors import QA_QUEUE_FAILURE
from ..schemas.qa import (
    QAPairCreateRequest,
    QAPairUpdateRequest,
    QAPairBatchCreateRequest,
    QAPairImportRequest,
    QAPairResponse,
    QAPairListResponse,
    QAPairBatchCreateResponse,
    QACategoryListResponse,
    compute_question_hash,
)

router = APIRouter()
logger = get_logger(__name__)


def is_duplicate_question(error: IntegrityError) -> bool:
    """Match only the question index, not unrelated integrity violations."""
    return "idx_qa_pairs_collection_question" in str(error.orig)


async def commit_qa_changes(db: AsyncSession) -> None:
    try:
        await db.commit()
    except IntegrityError as error:
        await db.rollback()
        if is_duplicate_question(error):
            raise HTTPException(
                status_code=400,
                detail="此知识库中已存在相同问题，请编辑已有问答。",
            ) from error
        raise


async def validate_qa_collection(
    db: AsyncSession,
    collection_id: UUID,
    project_id: UUID,
) -> Collection:
    """Validate that collection exists, belongs to project, and is QA type."""
    query = select(Collection).where(
        and_(
            Collection.id == collection_id,
            Collection.project_id == project_id,
            Collection.deleted_at.is_(None),
        )
    )
    result = await db.execute(query)
    collection = result.scalar_one_or_none()

    if not collection:
        raise HTTPException(
            status_code=404,
            detail=f"Collection {collection_id} not found"
        )

    if collection.collection_type != CollectionType.qa:
        raise HTTPException(
            status_code=400,
            detail=f"Collection {collection_id} is not a QA collection (type: {collection.collection_type.value})"
        )

    return collection


@router.post(
    "/collections/{collection_id}/qa-pairs",
    response_model=QAPairResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request or duplicate"},
        404: {"model": ErrorResponse, "description": "Collection not found"},
    },
    summary="Create a QA pair",
    description="Add a single question-answer pair to a QA collection.",
)
async def create_qa_pair(
    collection_id: UUID,
    request: QAPairCreateRequest,
    project_id: UUID = Query(..., description="Project ID"),
    db: AsyncSession = Depends(get_db_session_dependency),
):
    """Create a new QA pair in the specified collection."""
    # Validate collection
    await validate_qa_collection(db, collection_id, project_id)

    # Check for duplicate question
    question_hash = compute_question_hash(request.question)
    existing = await db.execute(
        select(QAPair.id).where(
            and_(
                QAPair.collection_id == collection_id,
                QAPair.question_hash == question_hash,
                QAPair.deleted_at.is_(None),
            )
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail="A QA pair with the same question already exists in this collection"
        )

    # Create QA pair
    qa_pair = QAPair(
        collection_id=collection_id,
        project_id=project_id,
        question=request.question,
        answer=request.answer,
        question_hash=question_hash,
        category=request.category,
        subcategory=request.subcategory,
        tags=request.tags,
        qa_metadata=request.qa_metadata,
        priority=request.priority,
        source_type="manual",
        status="pending",
    )
    db.add(qa_pair)
    await commit_qa_changes(db)
    await db.refresh(qa_pair)

    # Trigger async processing
    from ..tasks.qa_processing import process_qa_pair_task
    try:
        process_qa_pair_task.delay(str(qa_pair.id), str(project_id))
        logger.info(f"Queued QA pair {qa_pair.id} for processing")
    except Exception as e:
        logger.warning(
            "Failed to queue QA pair for processing", error_type=type(e).__name__,
        )
        qa_pair.status = "failed"
        qa_pair.error_message = QA_QUEUE_FAILURE
        await db.commit()
        await db.refresh(qa_pair)

    return QAPairResponse.model_validate(qa_pair)


@router.get(
    "/collections/{collection_id}/qa-pairs",
    response_model=QAPairListResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Collection not found"},
    },
    summary="List QA pairs",
    description="Get paginated list of QA pairs in a collection.",
)
async def list_qa_pairs(
    collection_id: UUID,
    project_id: UUID = Query(..., description="Project ID"),
    limit: int = Query(20, ge=1, le=100, description="Page size"),
    offset: int = Query(0, ge=0, description="Page offset"),
    category: Optional[str] = Query(None, description="Filter by category"),
    status: Optional[str] = Query(None, description="Filter by status"),
    db: AsyncSession = Depends(get_db_session_dependency),
):
    """List QA pairs in a collection with optional filtering."""
    # Validate collection
    await validate_qa_collection(db, collection_id, project_id)

    # Build query
    base_query = select(QAPair).where(
        and_(
            QAPair.collection_id == collection_id,
            QAPair.deleted_at.is_(None),
        )
    )

    if category:
        base_query = base_query.where(QAPair.category == category)
    if status:
        base_query = base_query.where(QAPair.status == status)

    # Get total count
    count_query = select(func.count()).select_from(base_query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Get paginated results
    query = base_query.order_by(QAPair.created_at.desc())
    query = query.limit(limit).offset(offset)
    result = await db.execute(query)
    qa_pairs = result.scalars().all()

    return QAPairListResponse(
        data=[QAPairResponse.model_validate(qa) for qa in qa_pairs],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/qa-categories",
    response_model=QACategoryListResponse,
    summary="List QA categories",
    description="Get distinct QA pair categories for a project, optionally filtered by collection.",
)
async def list_qa_categories(
    project_id: UUID = Query(..., description="Project ID"),
    collection_id: Optional[UUID] = Query(None, description="Optional collection ID to filter"),
    db: AsyncSession = Depends(get_db_session_dependency),
):
    """Get distinct categories from QA pairs for a project."""
    conditions = [
        QAPair.project_id == project_id,
        QAPair.deleted_at.is_(None),
        QAPair.category.isnot(None),
        QAPair.category != "",
    ]
    
    if collection_id:
        conditions.append(QAPair.collection_id == collection_id)
    
    query = (
        select(QAPair.category)
        .where(and_(*conditions))
        .distinct()
        .order_by(QAPair.category)
    )
    result = await db.execute(query)
    categories = [row[0] for row in result.fetchall()]

    return QACategoryListResponse(
        categories=categories,
        total=len(categories),
    )


@router.get(
    "/qa-pairs/{qa_pair_id}",
    response_model=QAPairResponse,
    responses={
        404: {"model": ErrorResponse, "description": "QA pair not found"},
    },
    summary="Get a QA pair",
    description="Get a single QA pair by ID.",
)
async def get_qa_pair(
    qa_pair_id: UUID,
    project_id: UUID = Query(..., description="Project ID"),
    db: AsyncSession = Depends(get_db_session_dependency),
):
    """Get a single QA pair by ID."""
    result = await db.execute(
        select(QAPair).where(
            and_(
                QAPair.id == qa_pair_id,
                QAPair.project_id == project_id,
                QAPair.deleted_at.is_(None),
            )
        )
    )
    qa_pair = result.scalar_one_or_none()

    if not qa_pair:
        raise HTTPException(status_code=404, detail="QA pair not found")

    return QAPairResponse.model_validate(qa_pair)


@router.put(
    "/qa-pairs/{qa_pair_id}",
    response_model=QAPairResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request"},
        404: {"model": ErrorResponse, "description": "QA pair not found"},
    },
    summary="Update a QA pair",
    description="Update an existing QA pair.",
)
async def update_qa_pair(
    qa_pair_id: UUID,
    request: QAPairUpdateRequest,
    project_id: UUID = Query(..., description="Project ID"),
    db: AsyncSession = Depends(get_db_session_dependency),
):
    """Update an existing QA pair."""
    result = await db.execute(
        select(QAPair).where(
            and_(
                QAPair.id == qa_pair_id,
                QAPair.project_id == project_id,
                QAPair.deleted_at.is_(None),
            )
        ).with_for_update()
    )
    qa_pair = result.scalar_one_or_none()

    if not qa_pair:
        raise HTTPException(status_code=404, detail="QA pair not found")

    # Track if content actually changed
    from ..services.knowledge_versions import find_source
    if await find_source(db, project_id, 'qa', qa_pair_id):
        raise HTTPException(409, '这份资料已启用版本管理，请使用“更新与历史版本”修改。')
    original_question = qa_pair.question
    original_answer = qa_pair.answer

    # Update fields
    if request.question is not None:
        qa_pair.question = request.question
        qa_pair.question_hash = compute_question_hash(request.question)
    if request.answer is not None:
        qa_pair.answer = request.answer
    if request.category is not None:
        qa_pair.category = request.category
    if request.subcategory is not None:
        qa_pair.subcategory = request.subcategory
    if request.tags is not None:
        qa_pair.tags = request.tags
    if request.qa_metadata is not None:
        qa_pair.qa_metadata = request.qa_metadata
    if request.priority is not None:
        qa_pair.priority = request.priority

    # Check if question or answer content actually changed
    content_changed = (
        qa_pair.question != original_question or 
        qa_pair.answer != original_answer
    )

    # A failed queue/provider attempt can be retried by saving the same content.
    needs_processing = content_changed or qa_pair.status == "failed"
    if content_changed:
        from ..services.knowledge_governance import KnowledgeGovernanceService
        await KnowledgeGovernanceService.invalidate_qa_review(
            db, project_id=project_id, qa_pair_id=qa_pair_id,
        )
    if needs_processing:
        qa_pair.status = "pending"
        qa_pair.error_message = None

    await commit_qa_changes(db)
    await db.refresh(qa_pair)

    # Trigger re-processing if content changed
    if needs_processing:
        from ..tasks.qa_processing import process_qa_pair_task
        try:
            process_qa_pair_task.delay(str(qa_pair.id), str(project_id), True)
            logger.info(f"Queued QA pair {qa_pair.id} for re-processing")
        except Exception as e:
            logger.warning(
                "Failed to queue QA pair for re-processing", error_type=type(e).__name__,
            )
            qa_pair.status = "failed"
            qa_pair.error_message = QA_QUEUE_FAILURE
            await db.commit()
            await db.refresh(qa_pair)

    return QAPairResponse.model_validate(qa_pair)


@router.delete(
    "/qa-pairs/{qa_pair_id}",
    status_code=204,
    responses={
        404: {"model": ErrorResponse, "description": "QA pair not found"},
    },
    summary="Delete a QA pair",
    description="Soft delete a QA pair and its associated document.",
)
async def delete_qa_pair(
    qa_pair_id: UUID,
    project_id: UUID = Query(..., description="Project ID"),
    db: AsyncSession = Depends(get_db_session_dependency),
):
    """Delete a QA pair (soft delete)."""
    result = await db.execute(
        select(QAPair).where(
            and_(
                QAPair.id == qa_pair_id,
                QAPair.project_id == project_id,
                QAPair.deleted_at.is_(None),
            )
        ).with_for_update()
    )
    qa_pair = result.scalar_one_or_none()

    if not qa_pair:
        raise HTTPException(status_code=404, detail="QA pair not found")

    # Remove the vector column and text with the QA deletion in one transaction.
    from ..services.qa_documents import remove_qa_document
    from datetime import datetime, timezone
    qa_pair.deleted_at = datetime.now(timezone.utc)
    await remove_qa_document(db, qa_pair)
    await db.commit()

    return None


@router.post(
    "/collections/{collection_id}/qa-pairs/batch",
    response_model=QAPairBatchCreateResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request"},
        404: {"model": ErrorResponse, "description": "Collection not found"},
    },
    summary="Batch create QA pairs",
    description="Create multiple QA pairs at once (max 1000).",
)
async def batch_create_qa_pairs(
    collection_id: UUID,
    request: QAPairBatchCreateRequest,
    project_id: UUID = Query(..., description="Project ID"),
    db: AsyncSession = Depends(get_db_session_dependency),
):
    """Batch create multiple QA pairs."""
    # Validate collection
    await validate_qa_collection(db, collection_id, project_id)

    created_ids = []
    skipped_count = 0
    failed_count = 0
    errors = []

    # Get existing question hashes
    existing_hashes = set()
    result = await db.execute(
        select(QAPair.question_hash).where(
            and_(
                QAPair.collection_id == collection_id,
                QAPair.deleted_at.is_(None),
            )
        )
    )
    existing_hashes = {row[0] for row in result.fetchall()}

    for idx, qa_request in enumerate(request.qa_pairs):
        try:
            question_hash = compute_question_hash(qa_request.question)

            # Skip duplicates
            if question_hash in existing_hashes:
                skipped_count += 1
                continue

            # Create QA pair
            qa_pair = QAPair(
                collection_id=collection_id,
                project_id=project_id,
                question=qa_request.question,
                answer=qa_request.answer,
                question_hash=question_hash,
                category=qa_request.category,
                subcategory=qa_request.subcategory,
                tags=qa_request.tags,
                qa_metadata=qa_request.qa_metadata,
                priority=qa_request.priority,
                source_type="import",
                status="pending",
            )
            # Isolate a rejected row so later rows do not inherit an aborted transaction.
            async with db.begin_nested():
                db.add(qa_pair)
                await db.flush()  # Get the ID
            created_ids.append(qa_pair.id)
            existing_hashes.add(question_hash)

        except IntegrityError as e:
            if is_duplicate_question(e):
                skipped_count += 1
                existing_hashes.add(question_hash)
            else:
                failed_count += 1
                errors.append({"index": idx, "error": "IntegrityError: 问答未通过数据库约束校验。"})
        except Exception as e:
            failed_count += 1
            errors.append({
                "index": idx,
                "error": f"{type(e).__name__}: 问答保存失败，请检查 RAG 服务日志。",
            })

    await db.commit()

    # Trigger batch processing
    if created_ids:
        from ..tasks.qa_processing import process_qa_pairs_batch_task
        try:
            process_qa_pairs_batch_task.delay(
                [str(qid) for qid in created_ids],
                str(project_id)
            )
            logger.info(f"Queued {len(created_ids)} QA pairs for batch processing")
        except Exception as e:
            logger.warning(
                "Failed to queue QA pairs for batch processing",
                error_type=type(e).__name__,
            )
            from sqlalchemy import update
            await db.execute(update(QAPair).where(
                QAPair.id.in_(created_ids), QAPair.project_id == project_id,
            ).values(status="failed", error_message=QA_QUEUE_FAILURE))
            await db.commit()
            failed_count += len(created_ids)
            errors.append({"stage": "queue", "error": "后台任务未能入队，请检查 RAG worker 和 Redis。"})

    return QAPairBatchCreateResponse(
        success=failed_count == 0,
        created_count=len(created_ids),
        skipped_count=skipped_count,
        failed_count=failed_count,
        created_ids=created_ids,
        errors=errors,
        message=f"Created {len(created_ids)} QA pairs, skipped {skipped_count} duplicates, {failed_count} failed",
    )


@router.post(
    "/collections/{collection_id}/qa-pairs/import",
    response_model=QAPairBatchCreateResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid format or data"},
        404: {"model": ErrorResponse, "description": "Collection not found"},
    },
    summary="Import QA pairs",
    description="Import QA pairs from JSON or CSV format.",
)
async def import_qa_pairs(
    collection_id: UUID,
    request: QAPairImportRequest,
    project_id: UUID = Query(..., description="Project ID"),
    db: AsyncSession = Depends(get_db_session_dependency),
):
    """Import QA pairs from JSON or CSV data."""
    # Validate collection
    await validate_qa_collection(db, collection_id, project_id)

    from ..services.qa_import import parse_qa_import
    try:
        qa_requests = parse_qa_import(request)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    # Use batch create logic
    batch_request = QAPairBatchCreateRequest(qa_pairs=qa_requests)
    return await batch_create_qa_pairs(collection_id, batch_request, project_id, db)

