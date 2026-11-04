"""Persist one staff-prepared internal intake without an external business write."""

import hashlib
import hmac
import json
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.aftersales_review import WorkflowAfterSalesReview
from app.models.approval import WorkflowApprovalRequest
from app.schemas.aftersales_intake import AfterSalesIntakeRequest, AfterSalesIntakeSummary
from app.services.aftersales_review import (
    AfterSalesReviewConflict, get_assigned_review, review_decision_digest,
)


def intake_payload_digest(review_id: str, request: AfterSalesIntakeRequest) -> str:
    """Use one canonical representation for concurrent and repeated submissions."""
    payload = json.dumps([
        "aftersales-intake-v1", review_id, request.review_digest,
        request.order_reference, request.sku, request.request_reason,
        request.policy_reference,
        sorted(str(file_id) for file_id in request.evidence_file_ids),
    ], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def intake_summary(review: WorkflowAfterSalesReview) -> AfterSalesIntakeSummary | None:
    if review.intake_digest is None:
        return None
    if (
        review.intake_order_reference is None or review.intake_sku is None
        or review.intake_request_reason is None or review.intake_policy_reference is None
        or review.intake_evidence_file_ids is None or review.intake_prepared_by is None
        or review.intake_prepared_at is None
    ):
        raise AfterSalesReviewConflict("内部售后登记回执不完整，请人工核查。")
    return AfterSalesIntakeSummary(
        review_id=review.id,
        order_reference=review.intake_order_reference,
        sku=review.intake_sku,
        request_reason=review.intake_request_reason,
        policy_reference=review.intake_policy_reference,
        evidence_file_ids=review.intake_evidence_file_ids,
        prepared_by=review.intake_prepared_by,
        prepared_at=review.intake_prepared_at,
    )


async def get_assigned_intake(
    db: AsyncSession, project_id: str, staff_id: str, review_id: str,
) -> AfterSalesIntakeSummary | None:
    review = await get_assigned_review(db, project_id, staff_id, review_id)
    if review is None:
        raise AfterSalesReviewConflict("售后核对待办不存在或不属于当前员工。")
    return intake_summary(review)


async def prepare_assigned_intake(
    db: AsyncSession, project_id: str, staff_id: str,
    review_id: str, request: AfterSalesIntakeRequest,
    *, now: datetime | None = None,
) -> AfterSalesIntakeSummary:
    """Store exactly one internal receipt for the reviewed, approved action."""
    review = await db.scalar(
        select(WorkflowAfterSalesReview)
        .join(WorkflowApprovalRequest,
              WorkflowApprovalRequest.id == WorkflowAfterSalesReview.approval_id)
        .where(
            WorkflowAfterSalesReview.id == review_id,
            WorkflowAfterSalesReview.project_id == project_id,
            WorkflowApprovalRequest.project_id == project_id,
            WorkflowApprovalRequest.approver_staff_id == staff_id,
        )
        .with_for_update(of=WorkflowAfterSalesReview)
        .execution_options(populate_existing=True)
    )
    if review is None:
        raise AfterSalesReviewConflict("售后核对待办不存在或不属于当前员工。")
    approval = await db.get(WorkflowApprovalRequest, review.approval_id)
    if (
        approval is None or approval.status != "approved"
        or approval.decided_by != staff_id or approval.decided_at is None
        or review.status != "reviewed"
        or review.review_result != "needs_manual_service"
        or review.reviewed_by != staff_id or review.reviewed_at is None
    ):
        raise AfterSalesReviewConflict("原审批或人工核对状态不允许准备售后登记。")
    if not hmac.compare_digest(review_decision_digest(review), request.review_digest):
        raise AfterSalesReviewConflict("售后核对内容已变化，请刷新后重新填写。")
    digest = intake_payload_digest(review_id, request)
    if review.intake_digest is not None:
        if review.intake_prepared_by != staff_id or not hmac.compare_digest(
            review.intake_digest, digest,
        ):
            raise AfterSalesReviewConflict("内部售后登记已保存其他内容，不能覆盖。")
        existing = intake_summary(review)
        assert existing is not None
        return existing
    files = sorted(str(file_id) for file_id in request.evidence_file_ids)
    changed = await db.scalar(
        update(WorkflowAfterSalesReview)
        .where(
            WorkflowAfterSalesReview.id == review_id,
            WorkflowAfterSalesReview.project_id == project_id,
            WorkflowAfterSalesReview.intake_digest.is_(None),
            WorkflowAfterSalesReview.status == "reviewed",
            WorkflowAfterSalesReview.review_result == "needs_manual_service",
        )
        .values(
            intake_order_reference=request.order_reference,
            intake_sku=request.sku,
            intake_request_reason=request.request_reason,
            intake_policy_reference=request.policy_reference,
            intake_evidence_file_ids=files,
            intake_digest=digest,
            intake_prepared_by=staff_id,
            intake_prepared_at=now or datetime.now(UTC),
        )
        .returning(WorkflowAfterSalesReview.id)
        .execution_options(synchronize_session=False)
    )
    if changed is None:
        await db.rollback()
        raise AfterSalesReviewConflict("内部售后登记已变化，请刷新后核对。")
    await db.commit()
    await db.refresh(review)
    saved = intake_summary(review)
    assert saved is not None
    return saved
