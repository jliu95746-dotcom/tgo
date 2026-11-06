"""Create a bounded internal staff task from the exact approved preview."""

import hmac
import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, case, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.aftersales_review import WorkflowAfterSalesReview
from app.models.approval import WorkflowApprovalRequest
from app.models.execution import WorkflowExecution
from app.services.execution_snapshot import definition_digest
from app.services.approval_preview import (
    approval_preview_digest, render_approval_preview,
)
from app.services.approval_retention import TERMINAL_EXECUTION_STATUSES
from app.services.approval_checkpoint import open_checkpoint


CASE_CATEGORIES = {
    "return_request": "退货申请",
    "exchange_request": "换货申请",
    "quality_issue": "质量问题",
}
ACTION_NAME = "create_aftersales_review"
REVIEW_RESULTS = frozenset({
    "needs_manual_service", "needs_customer_information", "not_aftersales",
})


class AfterSalesReviewConflict(RuntimeError):
    """The approved plan no longer matches the action to be performed."""


def review_decision_digest(review: WorkflowAfterSalesReview) -> str:
    """Identify the immutable task details shown in the decision dialog."""
    payload = json.dumps([
        "aftersales-review-decision-v1", review.id, review.project_id,
        review.execution_id, review.approval_id, review.visitor_session_id,
        review.category,
    ], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def list_assigned_reviews(
    db: AsyncSession, project_id: str, staff_id: str,
    *, limit: int = 100, before_status: str | None = None,
    before_created_at: datetime | None = None, before_id: str | None = None,
) -> list[WorkflowAfterSalesReview]:
    """Return only tasks whose approval belongs to this current staff member."""
    if not 1 <= limit <= 100:
        raise ValueError("售后待办分页大小无效")
    if any(value is not None for value in (before_status, before_created_at, before_id)):
        if (before_status not in {"pending_staff_review", "reviewed"}
                or before_created_at is None or not before_id):
            raise ValueError("售后待办分页位置不完整")
    priority = case((WorkflowAfterSalesReview.status == "pending_staff_review", 0), else_=1)
    query = (
        select(WorkflowAfterSalesReview)
        .join(
            WorkflowApprovalRequest,
            WorkflowApprovalRequest.id == WorkflowAfterSalesReview.approval_id,
        )
        .where(
            WorkflowAfterSalesReview.project_id == project_id,
            WorkflowApprovalRequest.project_id == project_id,
            WorkflowApprovalRequest.approver_staff_id == staff_id,
        )
        .order_by(priority, WorkflowAfterSalesReview.created_at.desc(),
                  WorkflowAfterSalesReview.id.desc())
        .limit(limit)
    )
    if before_status is not None and before_created_at is not None and before_id is not None:
        before_priority = 0 if before_status == "pending_staff_review" else 1
        query = query.where(or_(
            priority > before_priority,
            and_(priority == before_priority,
                 or_(WorkflowAfterSalesReview.created_at < before_created_at,
                     and_(WorkflowAfterSalesReview.created_at == before_created_at,
                          WorkflowAfterSalesReview.id < before_id))),
        ))
    rows = await db.scalars(query)
    return list(rows.all())


async def get_assigned_review(
    db: AsyncSession, project_id: str, staff_id: str, review_id: str,
) -> WorkflowAfterSalesReview | None:
    """Find one review using the original approver and tenant as authority."""
    return await db.scalar(
        select(WorkflowAfterSalesReview)
        .join(
            WorkflowApprovalRequest,
            WorkflowApprovalRequest.id == WorkflowAfterSalesReview.approval_id,
        )
        .where(
            WorkflowAfterSalesReview.id == review_id,
            WorkflowAfterSalesReview.project_id == project_id,
            WorkflowApprovalRequest.project_id == project_id,
            WorkflowApprovalRequest.approver_staff_id == staff_id,
        )
    )


async def decide_assigned_review(
    db: AsyncSession, project_id: str, actor_staff_id: str,
    review_id: str, result: str, expected_digest: str,
    *, now: datetime | None = None,
) -> WorkflowAfterSalesReview:
    """Store one staff triage result; this never submits an external case."""
    if result not in REVIEW_RESULTS:
        raise AfterSalesReviewConflict("售后核对结论无效。")
    review = await db.scalar(
        select(WorkflowAfterSalesReview)
        .join(
            WorkflowApprovalRequest,
            WorkflowApprovalRequest.id == WorkflowAfterSalesReview.approval_id,
        )
        .where(
            WorkflowAfterSalesReview.id == review_id,
            WorkflowAfterSalesReview.project_id == project_id,
            WorkflowApprovalRequest.project_id == project_id,
            WorkflowApprovalRequest.approver_staff_id == actor_staff_id,
        )
        .with_for_update(of=WorkflowAfterSalesReview)
        .execution_options(populate_existing=True)
    )
    if review is None:
        raise AfterSalesReviewConflict("售后核对待办不存在或不属于当前员工。")
    if not hmac.compare_digest(review_decision_digest(review), expected_digest):
        raise AfterSalesReviewConflict("售后核对内容已变化，请刷新后重新核对。")
    if review.status == "reviewed":
        if review.reviewed_by == actor_staff_id and review.review_result == result:
            return review
        raise AfterSalesReviewConflict("售后核对待办已由其他结论处理。")
    if review.status != "pending_staff_review":
        raise AfterSalesReviewConflict("售后核对待办状态无效。")
    decided_at = now or datetime.now(UTC)
    changed = await db.scalar(
        update(WorkflowAfterSalesReview)
        .where(
            WorkflowAfterSalesReview.id == review_id,
            WorkflowAfterSalesReview.project_id == project_id,
            WorkflowAfterSalesReview.status == "pending_staff_review",
        )
        .values(
            status="reviewed", review_result=result,
            reviewed_by=actor_staff_id, reviewed_at=decided_at,
        )
        .returning(WorkflowAfterSalesReview.id)
        .execution_options(synchronize_session=False)
    )
    if changed is None:
        await db.rollback()
        raise AfterSalesReviewConflict("售后核对待办已被处理，请刷新。")
    await db.commit()
    await db.refresh(review)
    return review


def _review_metadata(category: str, visitor_session_id: str) -> str:
    try:
        UUID(visitor_session_id)
    except (TypeError, ValueError):
        raise AfterSalesReviewConflict("售后核对任务缺少可信的客户会话。") from None
    if category not in CASE_CATEGORIES:
        raise AfterSalesReviewConflict("售后核对任务配置无效。")
    return (
        "；批准后创建内部售后核对待办；"
        f"类别：{CASE_CATEGORIES[category]}；客户会话：{visitor_session_id}"
    )


def review_preview(summary: str, category: str, visitor_session_id: str) -> str:
    """Show the exact internal action scope to the approving staff member."""
    if not summary.strip():
        raise AfterSalesReviewConflict("售后核对任务配置无效。")
    rendered = summary.strip() + _review_metadata(category, visitor_session_id)
    if len(rendered) > 500:
        raise AfterSalesReviewConflict("售后核对任务预览超过 500 字。")
    return rendered


def render_review_approval_preview(
    template: str, values: Mapping[str, object], category: str,
    visitor_session_id: str,
) -> str:
    """Keep a long customer request reviewable without losing its full identity."""
    placeholder = "{{input.question}}"
    if (template.count(placeholder) == 1 and template.count("{{") == 1
            and template.count("}}") == 1):
        question = values.get("input.question")
        if not isinstance(question, str) or not question.strip():
            raise ValueError("审批预览引用了缺失或非文本的流程变量")
        if "{{" in question or "}}" in question:
            raise ValueError("审批预览变量包含未解析的模板标记")
        prefix, suffix = template.split(placeholder)
        full_summary = f"{prefix}{question}{suffix}"
        metadata_length = len(_review_metadata(category, visitor_session_id))
        if len(full_summary.strip()) + metadata_length <= 500:
            return review_preview(
                render_approval_preview(template, values), category,
                visitor_session_id,
            )
        fingerprint = hashlib.sha256(question.encode("utf-8")).hexdigest()
        marker = (
            "…（已截取；原文 SHA-256：" + fingerprint
            + "。请在客户会话核对完整诉求）"
        )
        available = 500 - len(f"{prefix}{marker}{suffix}".strip()) - metadata_length
        if available < 1:
            raise AfterSalesReviewConflict("售后核对任务预览超过 500 字。")
        return review_preview(
            f"{prefix}{question[:available]}{marker}{suffix}",
            category, visitor_session_id,
        )
    return review_preview(
        render_approval_preview(template, values), category, visitor_session_id,
    )


async def create_approved_review(
    db: AsyncSession, project_id: str, execution_id: str, approval_id: str,
    *, expected_segment_started_at: datetime | None = None,
) -> WorkflowAfterSalesReview:
    """Submit at most one internal task; never call an external business system."""
    conditions = [
        WorkflowExecution.id == execution_id,
        WorkflowExecution.project_id == project_id,
    ]
    if expected_segment_started_at is not None:
        conditions.extend([
            WorkflowExecution.status == "running",
            WorkflowExecution.active_approval_id == approval_id,
            WorkflowExecution.active_segment_started_at
            == expected_segment_started_at,
        ])
    run = await db.scalar(
        select(WorkflowExecution).where(*conditions)
        .with_for_update().execution_options(populate_existing=True)
    )
    request = await db.scalar(
        select(WorkflowApprovalRequest).where(
            WorkflowApprovalRequest.id == approval_id,
            WorkflowApprovalRequest.project_id == project_id,
        ).with_for_update().execution_options(populate_existing=True)
    )
    if (
        run is None or request is None or run.project_id != project_id
        or request.project_id != project_id or request.execution_id != execution_id
        or request.status != "approved"
        or request.decided_by != request.approver_staff_id
        or request.decided_at is None
        or run.execution_mode != "production" or not run.employee_agent_id
        or not run.business_request_id
        or run.definition_snapshot is None or run.definition_digest is None
        or not hmac.compare_digest(
            definition_digest(run.definition_snapshot), run.definition_digest,
        ) or not run.visitor_session_id
    ):
        raise AfterSalesReviewConflict("原审批、客户会话或流程版本已变化，不能登记待办。")
    nodes = run.definition_snapshot.get("nodes", [])
    if not isinstance(nodes, list):
        raise AfterSalesReviewConflict("审批节点已变化，不能登记待办。")
    matches = [node for node in nodes if isinstance(node, dict)
               and node.get("id") == request.node_id and node.get("type") == "approval"]
    if len(matches) != 1:
        raise AfterSalesReviewConflict("审批节点已变化，不能登记待办。")
    data = matches[0]["data"]
    if not isinstance(data, dict):
        raise AfterSalesReviewConflict("审批节点已变化，不能登记待办。")
    if (
        run.status in TERMINAL_EXECUTION_STATUSES
        and isinstance(request.checkpoint, dict)
        and request.checkpoint.get("capture_status") == "redacted"
    ):
        receipt_digest = request.checkpoint.get("approval_preview_digest")
        expected_digest = approval_preview_digest(
            request.id, request.execution_id, request.summary,
        )
        receipt_category = data.get("case_category")
        if (
            not isinstance(receipt_digest, str)
            or not hmac.compare_digest(receipt_digest, expected_digest)
            or data.get("after_approval_action") != ACTION_NAME
            or not isinstance(receipt_category, str)
            or receipt_category not in CASE_CATEGORIES
        ):
            raise AfterSalesReviewConflict("审批回执或售后核对动作已变化。")
        existing = await db.scalar(select(WorkflowAfterSalesReview).where(
            WorkflowAfterSalesReview.project_id == project_id,
            WorkflowAfterSalesReview.approval_id == approval_id,
        ))
        if (
            existing is None or existing.execution_id != execution_id
            or existing.visitor_session_id != run.visitor_session_id
            or existing.category != receipt_category
        ):
            raise AfterSalesReviewConflict("原流程已结束，不能新增售后核对待办。")
        return existing
    try:
        saved = open_checkpoint(run, request)
    except ValueError:
        raise AfterSalesReviewConflict("审批检查点无效，不能登记待办。") from None
    if (
        saved.project_id != project_id or saved.execution_id != execution_id
        or saved.definition_digest != run.definition_digest
        or saved.input_digest != definition_digest(run.input or {})
        or saved.workflow_lineage != list(run.workflow_lineage or [run.workflow_id])
        or not saved.completed_nodes or saved.completed_nodes[-1] != request.node_id
    ):
        raise AfterSalesReviewConflict("审批检查点与原运行不一致。")
    if data.get("after_approval_action") != ACTION_NAME:
        raise AfterSalesReviewConflict("审批没有授权此售后核对动作。")
    category = data.get("case_category")
    summary_template = data.get("summary")
    if not isinstance(category, str) or category not in CASE_CATEGORIES:
        raise AfterSalesReviewConflict("售后核对任务类别无效。")
    if not isinstance(summary_template, str):
        raise AfterSalesReviewConflict("售后核对任务预览无效。")
    try:
        expected = render_review_approval_preview(
            summary_template, saved.context_data, category,
            run.visitor_session_id,
        )
    except (KeyError, TypeError, ValueError, AfterSalesReviewConflict):
        raise AfterSalesReviewConflict("审批预览无法复核，不能登记待办。") from None
    if not hmac.compare_digest(request.summary.encode("utf-8"), expected.encode("utf-8")):
        raise AfterSalesReviewConflict("审批预览与售后核对动作不一致。")
    existing = await db.scalar(select(WorkflowAfterSalesReview).where(
        WorkflowAfterSalesReview.project_id == project_id,
        WorkflowAfterSalesReview.approval_id == approval_id,
    ))
    if existing is not None:
        if (existing.execution_id != execution_id
                or existing.visitor_session_id != run.visitor_session_id
                or existing.category != category):
            raise AfterSalesReviewConflict("售后核对待办回执与原审批不一致。")
        return existing
    if run.status != "running":
        raise AfterSalesReviewConflict("原运行已结束，不能新增售后核对待办。")
    review = WorkflowAfterSalesReview(
        project_id=project_id, execution_id=execution_id, approval_id=approval_id,
        visitor_session_id=run.visitor_session_id, category=category,
        status="pending_staff_review",
    )
    try:
        async with db.begin_nested():
            db.add(review)
            await db.flush()
    except IntegrityError:
        existing = await db.scalar(select(WorkflowAfterSalesReview).where(
            WorkflowAfterSalesReview.project_id == project_id,
            WorkflowAfterSalesReview.approval_id == approval_id,
        ))
        if existing is None or existing.execution_id != execution_id:
            raise AfterSalesReviewConflict("售后核对待办写入状态未知，请人工核查。") from None
        return existing
    await db.commit()
    return review
