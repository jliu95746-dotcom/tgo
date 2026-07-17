"""Authenticated proxy endpoints for knowledge governance."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.security import get_current_active_user
from app.models.staff import Staff
from app.schemas.knowledge_governance import (
    KnowledgeGovernanceBackfillRequest,
    KnowledgeGovernanceBackfillResponse,
    KnowledgeGovernanceDraftRequest,
    KnowledgeGovernanceListResponse,
    KnowledgeGovernanceRecordResponse,
    KnowledgeGovernanceReviewRequest,
    KnowledgeReviewStatus,
)
from app.services.rag_client import rag_client

router = APIRouter()


@router.get("", response_model=KnowledgeGovernanceListResponse)
async def list_governance_records(
    collection_id: UUID = Query(...),
    review_status: KnowledgeReviewStatus | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: Staff = Depends(get_current_active_user),
) -> KnowledgeGovernanceListResponse:
    result = await rag_client.list_knowledge_governance(
        project_id=str(current_user.project_id),
        collection_id=str(collection_id),
        review_status=review_status.value if review_status else None,
        limit=limit,
        offset=offset,
    )
    return KnowledgeGovernanceListResponse.model_validate(result)


@router.put("/files/{file_id}", response_model=KnowledgeGovernanceRecordResponse)
async def save_file_governance_draft(
    file_id: UUID,
    request: KnowledgeGovernanceDraftRequest,
    current_user: Staff = Depends(get_current_active_user),
) -> KnowledgeGovernanceRecordResponse:
    result = await rag_client.save_file_knowledge_governance(
        project_id=str(current_user.project_id),
        file_id=str(file_id),
        data=request.model_dump(mode="json", exclude_none=True),
    )
    return KnowledgeGovernanceRecordResponse.model_validate(result)


@router.post("/{record_id}/submit", response_model=KnowledgeGovernanceRecordResponse)
async def submit_governance_record(
    record_id: UUID,
    current_user: Staff = Depends(get_current_active_user),
) -> KnowledgeGovernanceRecordResponse:
    result = await rag_client.submit_knowledge_governance(
        project_id=str(current_user.project_id),
        record_id=str(record_id),
    )
    return KnowledgeGovernanceRecordResponse.model_validate(result)


@router.post("/{record_id}/review", response_model=KnowledgeGovernanceRecordResponse)
async def review_governance_record(
    record_id: UUID,
    request: KnowledgeGovernanceReviewRequest,
    current_user: Staff = Depends(get_current_active_user),
) -> KnowledgeGovernanceRecordResponse:
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin permission required for knowledge review",
        )
    result = await rag_client.review_knowledge_governance(
        project_id=str(current_user.project_id),
        record_id=str(record_id),
        decision={
            "status": request.status.value,
            "reviewer": current_user.username,
            "reviewed_at": datetime.now(UTC).isoformat(),
        },
    )
    return KnowledgeGovernanceRecordResponse.model_validate(result)


@router.post("/backfill", response_model=KnowledgeGovernanceBackfillResponse)
async def backfill_governance_records(
    request: KnowledgeGovernanceBackfillRequest,
    current_user: Staff = Depends(get_current_active_user),
) -> KnowledgeGovernanceBackfillResponse:
    result = await rag_client.backfill_knowledge_governance(
        project_id=str(current_user.project_id),
        data=request.model_dump(mode="json", exclude_none=True),
    )
    return KnowledgeGovernanceBackfillResponse.model_validate(result)
