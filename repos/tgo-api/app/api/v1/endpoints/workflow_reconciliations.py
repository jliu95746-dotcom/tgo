"""Current staff's interrupted workflow inbox."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response

from app.core.security import get_current_active_user
from app.models import Staff
from app.schemas.workflow_reconciliation import (
    WorkflowReconciliationDecision,
    WorkflowReconciliationDecisionResult,
    WorkflowReconciliationSummary,
)
from app.services.workflow_client import workflow_client

router = APIRouter()


@router.get(
    "/reconciliations/{approval_id}/decisions/{review_digest}",
    response_model=WorkflowReconciliationDecisionResult,
)
async def get_my_reconciliation_decision(
    approval_id: UUID,
    response: Response,
    review_digest: str = Path(..., pattern=r"^[0-9a-f]{64}$"),
    current_user: Staff = Depends(get_current_active_user),
) -> WorkflowReconciliationDecisionResult:
    response.headers["Cache-Control"] = "no-store"
    return await workflow_client.get_reconciliation_decision(
        str(current_user.project_id),
        str(current_user.id),
        str(approval_id),
        review_digest,
    )


@router.get(
    "/reconciliations", response_model=list[WorkflowReconciliationSummary]
)
async def list_my_reconciliations(
    response: Response,
    limit: int = Query(30, ge=1, le=100),
    before_requested_at: datetime | None = Query(None),
    before_id: UUID | None = Query(None),
    current_user: Staff = Depends(get_current_active_user),
) -> list[WorkflowReconciliationSummary]:
    response.headers["Cache-Control"] = "no-store"
    if (before_requested_at is None) != (before_id is None):
        raise HTTPException(400, "核验分页位置不完整")
    return await workflow_client.list_reconciliations(
        str(current_user.project_id),
        str(current_user.id),
        limit=limit,
        before_requested_at=before_requested_at.isoformat()
        if before_requested_at
        else None,
        before_id=str(before_id) if before_id else None,
    )


@router.get(
    "/reconciliations/{approval_id}",
    response_model=WorkflowReconciliationSummary,
)
async def get_my_reconciliation(
    approval_id: UUID,
    response: Response,
    current_user: Staff = Depends(get_current_active_user),
) -> WorkflowReconciliationSummary:
    response.headers["Cache-Control"] = "no-store"
    return await workflow_client.get_reconciliation(
        str(current_user.project_id),
        str(current_user.id),
        str(approval_id),
    )


@router.post(
    "/reconciliations/{approval_id}/decision",
    response_model=WorkflowReconciliationDecisionResult,
)
async def decide_my_reconciliation(
    approval_id: UUID,
    request: WorkflowReconciliationDecision,
    response: Response,
    current_user: Staff = Depends(get_current_active_user),
) -> WorkflowReconciliationDecisionResult:
    response.headers["Cache-Control"] = "no-store"
    return await workflow_client.decide_reconciliation(
        str(current_user.project_id),
        str(current_user.id),
        str(approval_id),
        request,
    )
