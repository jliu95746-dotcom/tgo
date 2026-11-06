"""Internal workflow approval endpoints; staff identity comes from tgo-api."""

from datetime import datetime
from typing import Literal, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.workflow_internal_auth import require_internal_key
from app.config import settings as settings
from app.api.reconciliations import router as reconciliation_router
from app.database import get_db
from app.models.execution import WorkflowExecution
from app.services.approval_preview import approval_preview_digest
from app.services.aftersales_review import (
    AfterSalesReviewConflict,
    decide_assigned_review,
    get_assigned_review,
    list_assigned_reviews,
    review_decision_digest,
)
from app.schemas.aftersales_intake import (
    AfterSalesIntakeRequest,
    AfterSalesIntakeSummary,
)
from app.services.aftersales_intake import (
    get_assigned_intake,
    prepare_assigned_intake,
)
from app.models.aftersales_review import WorkflowAfterSalesReview
from app.services.approval_state import (
    ApprovalConflict,
    approved_resume_for_actor,
    decide_approval,
    approval_customer_session,
    get_assigned_approval_customer_session,
    list_assigned_approvals,
    reserve_resume_dispatch,
)
from app.services.workflow_dispatch import (
    WorkflowResumeDispatchError,
    enqueue_approved_resume,
)

__all__ = ["router", "settings"]

router = APIRouter()
router.include_router(reconciliation_router)


class ApprovalSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    execution_id: str
    node_id: str
    summary: str
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: str
    requested_at: datetime
    expires_at: datetime
    customer_session_available: bool = False


class ApprovalCustomerSession(BaseModel):
    visitor_session_id: str
    source_message_id: str | None = None
    source_channel: str | None = None


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ApprovalDecisionResult(BaseModel):
    id: str
    execution_id: str
    status: Literal["approved", "rejected"]
    dispatch_status: Literal["queued", "unknown", "not_applicable"]


class AfterSalesReviewSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    execution_id: str
    approval_id: str
    visitor_session_id: str
    category: Literal["return_request", "exchange_request", "quality_issue"]
    review_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["pending_staff_review", "reviewed"]
    created_at: datetime
    review_result: Literal[
        "needs_manual_service",
        "needs_customer_information",
        "not_aftersales",
    ] | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None


class AfterSalesReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result: Literal[
        "needs_manual_service",
        "needs_customer_information",
        "not_aftersales",
    ]
    review_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


def review_summary(row: WorkflowAfterSalesReview) -> AfterSalesReviewSummary:
    return AfterSalesReviewSummary(
        id=row.id,
        execution_id=row.execution_id,
        approval_id=row.approval_id,
        visitor_session_id=row.visitor_session_id,
        category=cast(
            Literal["return_request", "exchange_request", "quality_issue"],
            row.category,
        ),
        review_digest=review_decision_digest(row),
        status=cast(Literal["pending_staff_review", "reviewed"], row.status),
        created_at=row.created_at,
        review_result=cast(
            Literal[
                "needs_manual_service",
                "needs_customer_information",
                "not_aftersales",
            ]
            | None,
            row.review_result,
        ),
        reviewed_by=row.reviewed_by,
        reviewed_at=row.reviewed_at,
    )


@router.get(
    "/approvals/aftersales-reviews",
    response_model=list[AfterSalesReviewSummary],
)
async def list_my_aftersales_reviews(
    response: Response,
    project_id: str = Query(...),
    actor_staff_id: str = Query(...),
    limit: int = Query(100, ge=1, le=100),
    before_status: Literal["pending_staff_review", "reviewed"]
    | None = Query(None),
    before_created_at: datetime | None = Query(None),
    before_id: str | None = Query(None, min_length=1, max_length=36),
    x_internal_api_key: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> list[AfterSalesReviewSummary]:
    require_internal_key(x_internal_api_key)
    response.headers["Cache-Control"] = "no-store"
    if any(
        value is not None
        for value in (before_status, before_created_at, before_id)
    ) and not all(
        value is not None
        for value in (before_status, before_created_at, before_id)
    ):
        raise HTTPException(400, "售后待办分页位置不完整")
    rows = await list_assigned_reviews(
        db,
        project_id,
        actor_staff_id,
        limit=limit,
        before_status=before_status,
        before_created_at=before_created_at,
        before_id=before_id,
    )
    return [review_summary(row) for row in rows]


@router.get(
    "/approvals/aftersales-reviews/{review_id}",
    response_model=AfterSalesReviewSummary,
)
async def get_my_aftersales_review(
    review_id: str,
    response: Response,
    project_id: str = Query(...),
    actor_staff_id: str = Query(...),
    x_internal_api_key: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> AfterSalesReviewSummary:
    require_internal_key(x_internal_api_key)
    response.headers["Cache-Control"] = "no-store"
    row = await get_assigned_review(db, project_id, actor_staff_id, review_id)
    if row is None:
        raise HTTPException(404, "售后核对待办不存在或不属于当前员工。")
    return review_summary(row)


@router.get(
    "/approvals/aftersales-reviews/{review_id}/intake",
    response_model=AfterSalesIntakeSummary | None,
)
async def get_my_aftersales_intake(
    review_id: str,
    response: Response,
    project_id: str = Query(...),
    actor_staff_id: str = Query(...),
    x_internal_api_key: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> AfterSalesIntakeSummary | None:
    require_internal_key(x_internal_api_key)
    response.headers["Cache-Control"] = "no-store"
    try:
        return await get_assigned_intake(
            db, project_id, actor_staff_id, review_id
        )
    except AfterSalesReviewConflict as error:
        raise HTTPException(409, str(error)) from None


@router.post(
    "/approvals/aftersales-reviews/{review_id}/intake",
    response_model=AfterSalesIntakeSummary,
)
async def prepare_my_aftersales_intake(
    review_id: str,
    request: AfterSalesIntakeRequest,
    response: Response,
    project_id: str = Query(...),
    actor_staff_id: str = Query(...),
    x_internal_api_key: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> AfterSalesIntakeSummary:
    require_internal_key(x_internal_api_key)
    response.headers["Cache-Control"] = "no-store"
    try:
        return await prepare_assigned_intake(
            db,
            project_id,
            actor_staff_id,
            review_id,
            request,
        )
    except AfterSalesReviewConflict as error:
        raise HTTPException(409, str(error)) from None


@router.post(
    "/approvals/aftersales-reviews/{review_id}/decision",
    response_model=AfterSalesReviewSummary,
)
async def decide_my_aftersales_review(
    review_id: str,
    decision: AfterSalesReviewDecision,
    response: Response,
    project_id: str = Query(...),
    actor_staff_id: str = Query(...),
    x_internal_api_key: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> AfterSalesReviewSummary:
    require_internal_key(x_internal_api_key)
    response.headers["Cache-Control"] = "no-store"
    try:
        row = await decide_assigned_review(
            db,
            project_id,
            actor_staff_id,
            review_id,
            decision.result,
            decision.review_digest,
        )
    except AfterSalesReviewConflict as error:
        raise HTTPException(409, str(error)) from None
    return review_summary(row)


@router.get("/approvals", response_model=list[ApprovalSummary])
async def list_approvals(
    project_id: str = Query(...),
    actor_staff_id: str = Query(...),
    x_internal_api_key: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> list[ApprovalSummary]:
    require_internal_key(x_internal_api_key)
    rows = await list_assigned_approvals(db, project_id, actor_staff_id)
    runs = {}
    if rows:
        run_rows = await db.scalars(
            select(WorkflowExecution).where(
                WorkflowExecution.project_id == project_id,
                WorkflowExecution.id.in_([row.execution_id for row in rows]),
            )
        )
        runs = {run.id: run for run in run_rows}
    return [
        ApprovalSummary(
            id=row.id,
            execution_id=row.execution_id,
            node_id=row.node_id,
            summary=row.summary,
            preview_digest=approval_preview_digest(
                row.id, row.execution_id, row.summary
            ),
            status=row.status,
            requested_at=row.requested_at,
            expires_at=row.expires_at,
            customer_session_available=(
                row.execution_id in runs
                and approval_customer_session(runs[row.execution_id], row)
                is not None
            ),
        )
        for row in rows
    ]


@router.get(
    "/approvals/{approval_id}/customer-session",
    response_model=ApprovalCustomerSession,
)
async def get_my_approval_customer_session(
    approval_id: str,
    response: Response,
    project_id: str = Query(...),
    actor_staff_id: str = Query(...),
    x_internal_api_key: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> ApprovalCustomerSession:
    require_internal_key(x_internal_api_key)
    response.headers["Cache-Control"] = "no-store"
    origin = await get_assigned_approval_customer_session(
        db,
        project_id,
        actor_staff_id,
        approval_id,
    )
    if origin is None:
        raise HTTPException(404, "售后审批的客户会话不存在或已失效。")
    session_id, source_message_id, source_channel = origin
    return ApprovalCustomerSession(
        visitor_session_id=session_id,
        source_message_id=source_message_id,
        source_channel=source_channel,
    )


@router.post(
    "/approvals/{approval_id}/decision", response_model=ApprovalDecisionResult
)
async def decide_approval_request(
    approval_id: str,
    decision: ApprovalDecision,
    project_id: str = Query(...),
    actor_staff_id: str = Query(...),
    x_internal_api_key: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> ApprovalDecisionResult:
    require_internal_key(x_internal_api_key)
    try:
        try:
            request = await decide_approval(
                db,
                project_id,
                approval_id,
                actor_staff_id,
                decision.decision,
                expected_preview_digest=decision.preview_digest,
            )
            run = await db.get(WorkflowExecution, request.execution_id)
            if run is None:
                raise ApprovalConflict("原工作流已不存在。")
        except ApprovalConflict:
            if decision.decision != "approve":
                raise
            run, request = await approved_resume_for_actor(
                db,
                project_id,
                approval_id,
                actor_staff_id,
                decision.preview_digest,
            )
    except ApprovalConflict as error:
        raise HTTPException(409, str(error)) from None

    dispatch_status: Literal[
        "queued", "unknown", "not_applicable"
    ] = "not_applicable"
    if decision.decision == "approve":
        if await reserve_resume_dispatch(db, project_id, request.id):
            try:
                await enqueue_approved_resume(run, request)
                dispatch_status = "queued"
            except WorkflowResumeDispatchError:
                dispatch_status = "unknown"
        else:
            dispatch_status = "unknown"
    return ApprovalDecisionResult(
        id=request.id,
        execution_id=request.execution_id,
        status="approved" if decision.decision == "approve" else "rejected",
        dispatch_status=dispatch_status,
    )
