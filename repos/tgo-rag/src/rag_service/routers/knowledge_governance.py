"""Knowledge governance management endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db_session_dependency
from ..schemas.knowledge_governance import (
    KnowledgeGovernanceBackfillRequest,
    KnowledgeGovernanceBackfillResponse,
    KnowledgeGovernanceDraftRequest,
    KnowledgeGovernanceListResponse,
    KnowledgeGovernanceRecordResponse,
    KnowledgeReviewDecision,
    KnowledgeReviewStatus,
)
from ..services.knowledge_governance import (
    InvalidReviewTransitionError,
    KnowledgeGovernanceNotFoundError,
    KnowledgeGovernanceService,
)

router = APIRouter()


def _not_found(error: KnowledgeGovernanceNotFoundError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))


def _invalid_transition(error: InvalidReviewTransitionError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))


@router.get("", response_model=KnowledgeGovernanceListResponse)
async def list_governance_records(
    project_id: UUID = Query(..., description="Project ID"),
    collection_id: UUID = Query(..., description="Collection ID"),
    review_status: KnowledgeReviewStatus | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_session_dependency),
) -> KnowledgeGovernanceListResponse:
    """List governed files for one project collection."""
    return await KnowledgeGovernanceService.list_records(
        db,
        project_id=project_id,
        collection_id=collection_id,
        review_status=review_status,
        limit=limit,
        offset=offset,
    )


@router.put("/files/{file_id}", response_model=KnowledgeGovernanceRecordResponse)
async def save_file_governance_draft(
    file_id: UUID,
    data: KnowledgeGovernanceDraftRequest,
    project_id: UUID = Query(..., description="Project ID"),
    db: AsyncSession = Depends(get_db_session_dependency),
) -> KnowledgeGovernanceRecordResponse:
    """Create or update editable governance metadata for a file."""
    try:
        return await KnowledgeGovernanceService.upsert_file_draft(
            db,
            project_id=project_id,
            file_id=file_id,
            data=data,
        )
    except KnowledgeGovernanceNotFoundError as error:
        raise _not_found(error) from error
    except InvalidReviewTransitionError as error:
        raise _invalid_transition(error) from error


@router.post("/{record_id}/submit", response_model=KnowledgeGovernanceRecordResponse)
async def submit_governance_record(
    record_id: UUID,
    project_id: UUID = Query(..., description="Project ID"),
    db: AsyncSession = Depends(get_db_session_dependency),
) -> KnowledgeGovernanceRecordResponse:
    """Submit a draft or rejected record for administrator review."""
    try:
        return await KnowledgeGovernanceService.submit_record(
            db,
            project_id=project_id,
            record_id=record_id,
        )
    except KnowledgeGovernanceNotFoundError as error:
        raise _not_found(error) from error
    except InvalidReviewTransitionError as error:
        raise _invalid_transition(error) from error


@router.post("/{record_id}/review", response_model=KnowledgeGovernanceRecordResponse)
async def review_governance_record(
    record_id: UUID,
    decision: KnowledgeReviewDecision,
    project_id: UUID = Query(..., description="Project ID"),
    db: AsyncSession = Depends(get_db_session_dependency),
) -> KnowledgeGovernanceRecordResponse:
    """Approve, reject, or revoke a governance record with an audit actor."""
    try:
        return await KnowledgeGovernanceService.review_record(
            db,
            project_id=project_id,
            record_id=record_id,
            decision=decision,
        )
    except KnowledgeGovernanceNotFoundError as error:
        raise _not_found(error) from error
    except InvalidReviewTransitionError as error:
        raise _invalid_transition(error) from error


@router.post("/backfill", response_model=KnowledgeGovernanceBackfillResponse)
async def backfill_governance_records(
    data: KnowledgeGovernanceBackfillRequest,
    project_id: UUID = Query(..., description="Project ID"),
    db: AsyncSession = Depends(get_db_session_dependency),
) -> KnowledgeGovernanceBackfillResponse:
    """Create safe draft governance rows for ungoverned legacy files."""
    return await KnowledgeGovernanceService.backfill_files(
        db,
        project_id=project_id,
        data=data,
    )
