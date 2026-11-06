"""Internal inbox for an approval's exact interrupted continuation."""

from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.workflow_internal_auth import require_internal_key
from app.database import get_db
from app.integrations.api_client import verify_approval_assignees
from app.models.approval import WorkflowApprovalRequest
from app.models.execution import WorkflowExecution
from app.schemas.approval_reconciliation import (
    ReconciliationDecision,
    ReconciliationDecisionResult,
    ReconciliationSummary,
)
from app.services.approval_reconciliation import (
    ReconciliationConflict,
    decide_reconciliation,
    get_reconciliation,
    list_reconciliations,
    get_reconciliation_decision,
)
from app.services.approval_state import reserve_resume_dispatch
from app.services.workflow_dispatch import (
    WorkflowResumeDispatchError,
    enqueue_approved_resume,
)

router = APIRouter()


@router.get(
    "/approvals/reconciliations/{approval_id}/decisions/{review_digest}",
    response_model=ReconciliationDecisionResult,
)
async def read_my_reconciliation_decision(
    approval_id: str,
    review_digest: str,
    response: Response,
    project_id: str = Query(...),
    actor_staff_id: str = Query(...),
    x_internal_api_key: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> ReconciliationDecisionResult:
    require_internal_key(x_internal_api_key)
    response.headers["Cache-Control"] = "no-store"
    if len(review_digest) != 64 or any(
        char not in "0123456789abcdef" for char in review_digest
    ):
        raise HTTPException(422, "核验摘要格式无效")
    try:
        receipt = await get_reconciliation_decision(
            db, project_id, actor_staff_id, approval_id, review_digest
        )
    except ReconciliationConflict as error:
        raise HTTPException(409, str(error)) from None
    if receipt is None:
        raise HTTPException(404, "该次核验尚未形成处理回执。")
    return receipt


@router.get(
    "/approvals/reconciliations", response_model=list[ReconciliationSummary]
)
async def list_my_reconciliations(
    response: Response,
    project_id: str = Query(...),
    actor_staff_id: str = Query(...),
    limit: int = Query(30, ge=1, le=100),
    before_requested_at: datetime | None = Query(None),
    before_id: str | None = Query(None, min_length=1, max_length=36),
    x_internal_api_key: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> list[ReconciliationSummary]:
    require_internal_key(x_internal_api_key)
    response.headers["Cache-Control"] = "no-store"
    try:
        return await list_reconciliations(
            db,
            project_id,
            actor_staff_id,
            limit=limit,
            before_requested_at=before_requested_at,
            before_id=before_id,
        )
    except ReconciliationConflict as error:
        raise HTTPException(409, str(error)) from None


@router.get(
    "/approvals/reconciliations/{approval_id}",
    response_model=ReconciliationSummary,
)
async def get_my_reconciliation(
    approval_id: str,
    response: Response,
    project_id: str = Query(...),
    actor_staff_id: str = Query(...),
    x_internal_api_key: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> ReconciliationSummary:
    require_internal_key(x_internal_api_key)
    response.headers["Cache-Control"] = "no-store"
    try:
        return await get_reconciliation(
            db, project_id, actor_staff_id, approval_id
        )
    except ReconciliationConflict as error:
        raise HTTPException(409, str(error)) from None


@router.post(
    "/approvals/reconciliations/{approval_id}/decision",
    response_model=ReconciliationDecisionResult,
)
async def decide_my_reconciliation(
    approval_id: str,
    decision: ReconciliationDecision,
    response: Response,
    project_id: str = Query(...),
    actor_staff_id: str = Query(...),
    x_internal_api_key: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> ReconciliationDecisionResult:
    require_internal_key(x_internal_api_key)
    response.headers["Cache-Control"] = "no-store"
    try:
        await verify_approval_assignees(project_id, {actor_staff_id})
    except PermissionError:
        raise HTTPException(403, "原确认人已失去当前企业权限。") from None
    try:
        result = await decide_reconciliation(
            db,
            project_id,
            actor_staff_id,
            approval_id,
            decision.decision,
            decision.review_digest,
        )
    except ReconciliationConflict as error:
        await db.rollback()
        raise HTTPException(409, str(error)) from None
    if result.decision == "continue":
        run = await db.get(WorkflowExecution, result.execution_id)
        approval = await db.get(WorkflowApprovalRequest, approval_id)
        if (
            run is not None
            and approval is not None
            and run.status == "pending_resume"
        ):
            status = "unknown"
            if await reserve_resume_dispatch(db, project_id, approval_id):
                try:
                    await enqueue_approved_resume(run, approval)
                    status = "queued"
                except WorkflowResumeDispatchError:
                    pass
            return result.model_copy(update={"dispatch_status": status})
    return result
