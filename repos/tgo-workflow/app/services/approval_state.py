"""Commit approval requests and run transitions together; no external action here."""

import hmac
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.engine.checkpoint import ExecutionCheckpoint
from app.models.approval import WorkflowApprovalRequest
from app.models.execution import WorkflowExecution
from app.services.approval_preview import approval_preview_digest, render_approval_preview
from app.services.aftersales_review import (
    ACTION_NAME, AfterSalesReviewConflict, render_review_approval_preview,
)
from app.services.execution_snapshot import definition_digest
from app.services.approval_retention import scrub_terminal_approval_checkpoints
from app.services.webhook_lifecycle import scrub_webhook_inputs
from app.services.approval_checkpoint import (
    ApprovalCheckpointKeyUnavailable, open_checkpoint, seal_checkpoint,
    CheckpointCryptoError,
)
from app.services.approval_inputs import approval_input_digest
from app.services.approval_resume_progress import selected_resume_checkpoint


class ApprovalConflict(RuntimeError):
    """The request is not current, not owned, or not eligible for a decision."""


RESUME_DISPATCH_INTERVAL = timedelta(minutes=2)


def approval_customer_session(
    run: WorkflowExecution, request: WorkflowApprovalRequest,
) -> str | None:
    """Expose the original session only for an active, frozen aftersales approval."""
    if (
        run.project_id != request.project_id
        or run.id != request.execution_id
        or run.active_approval_id != request.id
        or (request.status, run.status) not in {
            ("pending", "waiting_approval"), ("approved", "pending_resume"),
        }
        or run.execution_mode != "production"
        or not run.employee_agent_id or not run.business_request_id
        or not run.visitor_session_id or not isinstance(run.definition_snapshot, dict)
        or not run.definition_digest
    ):
        return None
    try:
        UUID(run.visitor_session_id)
        if definition_digest(run.definition_snapshot) != run.definition_digest:
            return None
    except (ValueError, TypeError):
        return None
    nodes = run.definition_snapshot.get("nodes")
    if not isinstance(nodes, list):
        return None
    matches = [
        node for node in nodes
        if isinstance(node, dict) and node.get("id") == request.node_id
        and node.get("type") == "approval"
    ]
    if len(matches) != 1 or not isinstance(matches[0].get("data"), dict):
        return None
    if matches[0]["data"].get("after_approval_action") != ACTION_NAME:
        return None
    return run.visitor_session_id


async def get_assigned_approval_customer_session(
    db: AsyncSession, project_id: str, actor_staff_id: str, approval_id: str,
    *, now: datetime | None = None,
) -> tuple[str, str | None, str | None] | None:
    """Recheck ownership and the run at click time, without a list-page cap."""
    current = _as_utc(now or datetime.now(UTC))
    result = await db.execute(
        select(WorkflowApprovalRequest, WorkflowExecution)
        .join(WorkflowExecution, WorkflowExecution.id == WorkflowApprovalRequest.execution_id)
        .where(
            WorkflowApprovalRequest.id == approval_id,
            WorkflowApprovalRequest.project_id == project_id,
            WorkflowApprovalRequest.approver_staff_id == actor_staff_id,
            WorkflowExecution.project_id == project_id,
            WorkflowExecution.active_approval_id == approval_id,
            or_(
                and_(
                    WorkflowApprovalRequest.status == "pending",
                    WorkflowApprovalRequest.expires_at > current,
                    WorkflowExecution.status == "waiting_approval",
                ),
                and_(
                    WorkflowApprovalRequest.status == "approved",
                    WorkflowExecution.status == "pending_resume",
                ),
            ),
        )
    )
    pair = result.one_or_none()
    if pair is None:
        return None
    request, run = pair
    session_id = approval_customer_session(run, request)
    if session_id is None:
        return None
    return session_id, run.source_message_id, run.source_channel


def _as_utc(value: datetime) -> datetime:
    # SQLite loses timezone information on round-trip; PostgreSQL keeps it.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def pause_for_approval(
    db: AsyncSession,
    project_id: str,
    execution_id: str,
    node_id: str,
    approver_staff_id: str,
    summary: str,
    checkpoint: ExecutionCheckpoint,
    *,
    expires_at: datetime,
    now: datetime | None = None,
    expected_segment_started_at: datetime | None = None,
) -> WorkflowApprovalRequest:
    """Move a running workflow to a durable wait after its node has completed."""
    current = _as_utc(now or datetime.now(UTC))
    if (
        expires_at.tzinfo is None or expires_at <= current
        or expires_at > current + timedelta(days=7)
        or not summary.strip() or len(summary) > 500
        or not node_id or not checkpoint.completed_nodes
        or checkpoint.completed_nodes[-1] != node_id
    ):
        raise ApprovalConflict("审批配置或截止时间无效，请重新检查流程。")
    try:
        UUID(approver_staff_id)
    except ValueError:
        raise ApprovalConflict("审批人编号无效。") from None
    run = await db.get(WorkflowExecution, execution_id)
    if (
        run is None or run.project_id != project_id or run.status != "running"
        or run.definition_snapshot is None or run.definition_digest is None
        or definition_digest(run.definition_snapshot) != run.definition_digest
        or checkpoint.project_id != project_id
        or checkpoint.execution_id != execution_id
        or checkpoint.definition_digest != run.definition_digest
        or checkpoint.workflow_lineage != list(run.workflow_lineage or [run.workflow_id])
    ):
        raise ApprovalConflict("运行记录或检查点已变化，不能发起审批。")
    try:
        expected_input = await approval_input_digest(db, run)
    except ValueError:
        raise ApprovalConflict("审批冻结输入无法核验。") from None
    if checkpoint.input_digest != expected_input:
        raise ApprovalConflict("运行输入或检查点已变化，不能发起审批。")
    nodes = run.definition_snapshot.get("nodes", [])
    if not isinstance(nodes, list):
        raise ApprovalConflict("检查点不属于审批节点。")
    matching_nodes = [
        node for node in nodes
        if isinstance(node, dict) and node.get("id") == node_id
        and node.get("type") == "approval"
    ]
    if len(matching_nodes) != 1:
        raise ApprovalConflict("检查点不属于审批节点。")
    node_data = matching_nodes[0].get("data")
    if not isinstance(node_data, dict):
        raise ApprovalConflict("审批预览定义无效。")
    summary_template = node_data.get("summary")
    if not isinstance(summary_template, str):
        raise ApprovalConflict("审批预览定义无效。")
    try:
        if node_data.get("after_approval_action") == ACTION_NAME:
            category = node_data.get("case_category")
            if not isinstance(category, str) or not run.visitor_session_id:
                raise ApprovalConflict("售后核对任务缺少类别或客户会话。")
            expected_summary = render_review_approval_preview(
                summary_template, checkpoint.context_data, category,
                run.visitor_session_id,
            )
        else:
            expected_summary = render_approval_preview(
                summary_template, checkpoint.context_data,
            )
    except (ValueError, AfterSalesReviewConflict) as exc:
        raise ApprovalConflict(str(exc)) from exc
    if summary.strip() != expected_summary:
        raise ApprovalConflict("审批预览与本次运行内容不一致。")
    request = WorkflowApprovalRequest(
        id=str(uuid4()),
        project_id=project_id, execution_id=execution_id, node_id=node_id,
        approver_staff_id=approver_staff_id, summary=expected_summary,
        status="pending", expires_at=expires_at,
    )
    request.checkpoint = seal_checkpoint(run, request, checkpoint)
    db.add(request)
    previous_approval: ColumnElement[bool]
    if expected_segment_started_at is None:
        previous_approval = WorkflowExecution.active_approval_id.is_(None)
    else:
        previous_approval = and_(
            WorkflowExecution.active_segment_started_at
            == expected_segment_started_at,
            select(WorkflowApprovalRequest.id).where(
                WorkflowApprovalRequest.id
                == WorkflowExecution.active_approval_id,
                WorkflowApprovalRequest.project_id == project_id,
                WorkflowApprovalRequest.execution_id == execution_id,
                WorkflowApprovalRequest.status == "approved",
            ).exists(),
        )
    try:
        await db.flush()
        changed = await db.scalar(
            update(WorkflowExecution)
            .where(
                WorkflowExecution.id == execution_id,
                WorkflowExecution.project_id == project_id,
                WorkflowExecution.status == "running",
                previous_approval,
            )
            .values(status="waiting_approval", active_approval_id=request.id)
            .returning(WorkflowExecution.id)
        )
        if changed is None:
            raise ApprovalConflict("工作流已结束或已有待确认请求。")
        await db.commit()
    except (IntegrityError, ApprovalConflict):
        await db.rollback()
        raise ApprovalConflict("工作流已有待确认请求或状态已变化。") from None
    return request


async def decide_approval(
    db: AsyncSession,
    project_id: str,
    approval_id: str,
    actor_staff_id: str,
    decision: Literal["approve", "reject"],
    *,
    expected_preview_digest: str,
    now: datetime | None = None,
) -> WorkflowApprovalRequest:
    """Only the assigned staff may consume a pending approval once."""
    current = _as_utc(now or datetime.now(UTC))
    if decision not in ("approve", "reject"):
        raise ApprovalConflict("审批决定无效。")
    request = await db.get(WorkflowApprovalRequest, approval_id)
    if (
        request is None or request.project_id != project_id
        or request.approver_staff_id != actor_staff_id
        or request.status != "pending" or _as_utc(request.expires_at) <= current
    ):
        raise ApprovalConflict("审批请求不存在、已过期或不属于当前审批人。")
    if not hmac.compare_digest(
        expected_preview_digest,
        approval_preview_digest(request.id, request.execution_id, request.summary),
    ):
        raise ApprovalConflict("审批预览已变化，请刷新后重新核对。")
    result_status = "approved" if decision == "approve" else "rejected"
    run_status = "pending_resume" if decision == "approve" else "rejected"
    changed_run = await db.scalar(
        update(WorkflowExecution)
        .where(
            WorkflowExecution.id == request.execution_id,
            WorkflowExecution.project_id == project_id,
            WorkflowExecution.status == "waiting_approval",
            WorkflowExecution.active_approval_id == approval_id,
        )
        .values(
            status=run_status,
            completed_at=current if decision == "reject" else None,
            error="审批被拒绝。" if decision == "reject" else None,
            **({"input": None} if decision == "reject" else {}),
        )
        .returning(WorkflowExecution.id)
    )
    if changed_run is None:
        await db.rollback()
        raise ApprovalConflict("工作流已变更，审批决定没有生效。")
    changed = await db.scalar(
        update(WorkflowApprovalRequest)
        .where(
            WorkflowApprovalRequest.id == approval_id,
            WorkflowApprovalRequest.project_id == project_id,
            WorkflowApprovalRequest.approver_staff_id == actor_staff_id,
            WorkflowApprovalRequest.status == "pending",
            WorkflowApprovalRequest.summary == request.summary,
            WorkflowApprovalRequest.expires_at > current,
        )
        .values(status=result_status, decided_at=current, decided_by=actor_staff_id)
        .returning(WorkflowApprovalRequest.id)
        .execution_options(synchronize_session=False)
    )
    if changed is None:
        await db.rollback()
        raise ApprovalConflict("审批请求已处理或已过期。")
    if decision == "reject":
        await scrub_terminal_approval_checkpoints(
            db, project_id, request.execution_id,
        )
        await scrub_webhook_inputs(db, project_id, request.execution_id)
    await db.commit()
    await db.refresh(request)
    return request


async def expire_approval(
    db: AsyncSession,
    project_id: str,
    approval_id: str,
    *,
    now: datetime | None = None,
) -> bool:
    """Expire only a still-waiting approval; a decided request stays decided."""
    current = _as_utc(now or datetime.now(UTC))
    request = await db.get(WorkflowApprovalRequest, approval_id)
    if (
        request is None or request.project_id != project_id
        or request.status != "pending"
        or _as_utc(request.expires_at) > current
    ):
        return False
    changed_run = await db.scalar(
        update(WorkflowExecution)
        .where(
            WorkflowExecution.id == request.execution_id,
            WorkflowExecution.project_id == project_id,
            WorkflowExecution.status == "waiting_approval",
            WorkflowExecution.active_approval_id == approval_id,
        )
        .values(
            status="expired", input=None, completed_at=current,
            error="审批已过期。",
        )
        .returning(WorkflowExecution.id)
    )
    if changed_run is None:
        await db.rollback()
        return False
    changed = await db.scalar(
        update(WorkflowApprovalRequest)
        .where(
            WorkflowApprovalRequest.id == approval_id,
            WorkflowApprovalRequest.project_id == project_id,
            WorkflowApprovalRequest.status == "pending",
            WorkflowApprovalRequest.expires_at <= current,
        )
        .values(status="expired", decided_at=current)
        .returning(WorkflowApprovalRequest.id)
        .execution_options(synchronize_session=False)
    )
    if changed is None:
        await db.rollback()
        return False
    await scrub_terminal_approval_checkpoints(
        db, project_id, request.execution_id,
    )
    await scrub_webhook_inputs(db, project_id, request.execution_id)
    await db.commit()
    return True


async def claim_approved_resume(
    db: AsyncSession,
    project_id: str,
    execution_id: str,
    workflow_id: str,
    *, expected_approval_id: str, expected_approver_id: str,
    segment_started_at: datetime | None = None,
) -> ExecutionCheckpoint | None:
    """Claim one approved continuation without replaying an earlier node."""
    run = await db.get(WorkflowExecution, execution_id)
    if (
        run is None or run.project_id != project_id
        or run.workflow_id != workflow_id
        or run.status != "pending_resume"
        or run.active_approval_id != expected_approval_id
    ):
        await db.commit()
        return None
    request = await db.get(WorkflowApprovalRequest, run.active_approval_id)
    if (
        request is None or request.project_id != project_id
        or request.execution_id != execution_id or request.status != "approved"
        or run.definition_digest is None
    ):
        raise ApprovalConflict("批准记录或原流程已变化，不能恢复执行。")
    if request.approver_staff_id != expected_approver_id:
        await db.commit()
        return None
    try:
        checkpoint = open_checkpoint(run, request)
        expected_input = await approval_input_digest(db, run)
    except ApprovalCheckpointKeyUnavailable:
        # A concurrent source stop or another claim must win over this notice.
        await db.execute(
            update(WorkflowExecution)
            .where(
                WorkflowExecution.id == execution_id,
                WorkflowExecution.project_id == project_id,
                WorkflowExecution.workflow_id == workflow_id,
                WorkflowExecution.status == "pending_resume",
                WorkflowExecution.active_approval_id == expected_approval_id,
                select(WorkflowApprovalRequest.id).where(
                    WorkflowApprovalRequest.id == expected_approval_id,
                    WorkflowApprovalRequest.project_id == project_id,
                    WorkflowApprovalRequest.execution_id == execution_id,
                    WorkflowApprovalRequest.approver_staff_id == expected_approver_id,
                    WorkflowApprovalRequest.status == "approved",
                ).exists(),
            )
            .values(error="审批检查点保管密钥暂不可用，请恢复原密钥；原审批与密文保留等待恢复。")
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        return None
    except ValueError:
        raise ApprovalConflict("批准检查点无效，不能恢复执行。") from None
    if (
        checkpoint.project_id != project_id
        or checkpoint.execution_id != execution_id
        or checkpoint.definition_digest != run.definition_digest
        or checkpoint.input_digest != expected_input
        or checkpoint.workflow_lineage != list(run.workflow_lineage or [workflow_id])
        or not checkpoint.completed_nodes
        or checkpoint.completed_nodes[-1] != request.node_id
    ):
        raise ApprovalConflict("批准检查点与当前运行不一致。")
    try:
        selected = await selected_resume_checkpoint(
            db, run, request, checkpoint,
        )
    except ApprovalCheckpointKeyUnavailable:
        await db.execute(
            update(WorkflowExecution)
            .where(
                WorkflowExecution.id == execution_id,
                WorkflowExecution.project_id == project_id,
                WorkflowExecution.workflow_id == workflow_id,
                WorkflowExecution.status == "pending_resume",
                WorkflowExecution.active_approval_id == expected_approval_id,
            )
            .values(
                error="恢复记录所需历史保管密钥暂不可用，原记录保留等待恢复。"
            )
        )
        await db.commit()
        return None
    except (CheckpointCryptoError, ValueError):
        raise ApprovalConflict("已核验的恢复位置已变化，不能继续执行。") from None
    changed = await db.scalar(
        update(WorkflowExecution)
        .where(
            WorkflowExecution.id == execution_id,
            WorkflowExecution.project_id == project_id,
            WorkflowExecution.workflow_id == workflow_id,
            WorkflowExecution.status == "pending_resume",
            WorkflowExecution.active_approval_id == request.id,
        )
        .values(
            status="running",
            active_segment_started_at=segment_started_at or datetime.now(UTC),
            error=None,
        )
        .returning(WorkflowExecution.id)
    )
    claimed = changed is not None
    await db.commit()
    return selected if claimed else None


async def approved_resume_assignee(
    db: AsyncSession, project_id: str, execution_id: str, workflow_id: str,
) -> tuple[str, str] | None:
    """Read the exact approved request before contacting the staff authority."""
    row = await db.execute(
        select(WorkflowApprovalRequest.id, WorkflowApprovalRequest.approver_staff_id)
        .join(WorkflowExecution, WorkflowExecution.active_approval_id == WorkflowApprovalRequest.id)
        .where(
            WorkflowExecution.id == execution_id,
            WorkflowExecution.project_id == project_id,
            WorkflowExecution.workflow_id == workflow_id,
            WorkflowExecution.status == "pending_resume",
            WorkflowApprovalRequest.project_id == project_id,
            WorkflowApprovalRequest.execution_id == execution_id,
            WorkflowApprovalRequest.status == "approved",
        )
    )
    value = row.one_or_none()
    await db.commit()
    return (value[0], value[1]) if value is not None else None


async def fail_revoked_approved_resume(
    db: AsyncSession, project_id: str, execution_id: str, approval_id: str,
) -> bool:
    """End only the same still-unclaimed approved run after revocation."""
    changed = await db.scalar(
        update(WorkflowExecution)
        .where(
            WorkflowExecution.id == execution_id,
            WorkflowExecution.project_id == project_id,
            WorkflowExecution.status == "pending_resume",
            WorkflowExecution.active_approval_id == approval_id,
        )
        .values(
            status="failed", input=None, completed_at=datetime.now(UTC),
            error="审批员工已失去本企业权限，流程停止。",
        )
        .returning(WorkflowExecution.id)
    )
    if changed is not None:
        await scrub_terminal_approval_checkpoints(db, project_id, execution_id)
        await scrub_webhook_inputs(db, project_id, execution_id)
    await db.commit()
    return changed is not None


async def list_assigned_approvals(
    db: AsyncSession, project_id: str, actor_staff_id: str,
    *, now: datetime | None = None,
) -> list[WorkflowApprovalRequest]:
    """Show actionable waits and approved runs still awaiting dispatch."""
    current = _as_utc(now or datetime.now(UTC))
    result = await db.scalars(
        select(WorkflowApprovalRequest)
        .join(WorkflowExecution, WorkflowExecution.id == WorkflowApprovalRequest.execution_id)
        .where(
            WorkflowApprovalRequest.project_id == project_id,
            WorkflowApprovalRequest.approver_staff_id == actor_staff_id,
            WorkflowExecution.project_id == project_id,
            WorkflowExecution.active_approval_id == WorkflowApprovalRequest.id,
            or_(
                and_(
                    WorkflowApprovalRequest.status == "pending",
                    WorkflowApprovalRequest.expires_at > current,
                    WorkflowExecution.status == "waiting_approval",
                ),
                and_(
                    WorkflowApprovalRequest.status == "approved",
                    WorkflowExecution.status == "pending_resume",
                ),
            ),
        )
        .order_by(WorkflowApprovalRequest.requested_at.desc())
        .limit(100)
    )
    return list(result.all())


async def approved_resume_for_actor(
    db: AsyncSession, project_id: str, approval_id: str,
    actor_staff_id: str, expected_preview_digest: str,
) -> tuple[WorkflowExecution, WorkflowApprovalRequest]:
    """Permit a broker retry only while this approved run still awaits a worker."""
    request = await db.get(WorkflowApprovalRequest, approval_id)
    if (
        request is None or request.project_id != project_id
        or request.approver_staff_id != actor_staff_id
        or request.status != "approved"
    ):
        raise ApprovalConflict("审批请求不是当前员工已批准的待恢复请求。")
    if not hmac.compare_digest(
        expected_preview_digest,
        approval_preview_digest(request.id, request.execution_id, request.summary),
    ):
        raise ApprovalConflict("审批预览已变化，请刷新后重新核对。")
    run = await db.get(WorkflowExecution, request.execution_id)
    if (
        run is None or run.project_id != project_id
        or run.status != "pending_resume"
        or run.active_approval_id != request.id
    ):
        raise ApprovalConflict("工作流已恢复或状态已变化，不能再次派发。")
    return run, request


async def reserve_resume_dispatch(
    db: AsyncSession, project_id: str, approval_id: str,
    *, now: datetime | None = None,
) -> bool:
    """Lease one delivery attempt only while its exact approved run still waits."""
    current = _as_utc(now or datetime.now(UTC))
    eligible_run = select(WorkflowExecution.id).where(
        WorkflowExecution.id == WorkflowApprovalRequest.execution_id,
        WorkflowExecution.project_id == project_id,
        WorkflowExecution.status == "pending_resume",
        WorkflowExecution.active_approval_id == approval_id,
    ).exists()
    changed = await db.scalar(
        update(WorkflowApprovalRequest)
        .where(
            WorkflowApprovalRequest.id == approval_id,
            WorkflowApprovalRequest.project_id == project_id,
            WorkflowApprovalRequest.status == "approved",
            or_(
                WorkflowApprovalRequest.last_resume_dispatch_at.is_(None),
                WorkflowApprovalRequest.last_resume_dispatch_at
                <= current - RESUME_DISPATCH_INTERVAL,
            ),
            eligible_run,
        )
        .values(
            last_resume_dispatch_at=current,
            resume_dispatch_attempts=WorkflowApprovalRequest.resume_dispatch_attempts + 1,
        )
        .returning(WorkflowApprovalRequest.id)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return changed is not None
