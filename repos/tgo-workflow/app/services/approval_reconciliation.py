"""Resolve an assigned interrupted execution against an immutable preview."""

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Literal

from pydantic import ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.approval import WorkflowApprovalRequest
from app.models.execution import WorkflowExecution
from app.schemas.approval_reconciliation import (
    ReconciliationDecisionRecord,
    ReconciliationDecisionResult,
    ReconciliationLedger,
    ReconciliationNodeSummary,
    ReconciliationSummary,
)
from app.services.approval_checkpoint import (
    ApprovalCheckpointKeyUnavailable,
    CheckpointCryptoError,
    VERIFIED_RESUME_CAPTURE,
)
from app.services.approval_resume_progress import (
    progress_digest,
    resume_receipts,
    verified_resume_progress,
)
from app.services.approval_resume_recovery import RECONCILIATION_REQUIRED
from app.services.approval_retention import scrub_terminal_approval_checkpoints
from app.services.execution_snapshot import definition_digest
from app.services.webhook_lifecycle import scrub_webhook_inputs


class ReconciliationConflict(RuntimeError):
    """Only the assigned staff may resolve the unchanged interrupted run."""


def _ledger(request: WorkflowApprovalRequest) -> ReconciliationLedger:
    try:
        return ReconciliationLedger.model_validate(
            request.reconciliation_ledger or {}
        )
    except ValidationError:
        raise ReconciliationConflict("核验记录格式无效，请保留原记录并联系管理员。") from None


def _pending(run: WorkflowExecution, request: WorkflowApprovalRequest) -> bool:
    return (
        run.active_approval_id == request.id
        and run.status == "running"
        and run.active_segment_started_at is None
        and run.error == RECONCILIATION_REQUIRED
        and request.status == "approved"
        and request.decided_at is not None
        and request.decided_by == request.approver_staff_id
    )


async def _pair(
    db: AsyncSession,
    project: str,
    actor: str,
    approval: str,
    *,
    lock: bool = False,
) -> tuple[WorkflowExecution, WorkflowApprovalRequest]:
    request = await db.scalar(
        select(WorkflowApprovalRequest)
        .where(
            WorkflowApprovalRequest.id == approval,
            WorkflowApprovalRequest.project_id == project,
            WorkflowApprovalRequest.approver_staff_id == actor,
        )
        .execution_options(populate_existing=True)
    )
    if request is None:
        raise ReconciliationConflict("核验任务不存在或不属于当前员工。")
    query = (
        select(WorkflowExecution)
        .where(
            WorkflowExecution.id == request.execution_id,
            WorkflowExecution.project_id == project,
        )
        .execution_options(populate_existing=True)
    )
    if lock:
        query = query.with_for_update()
    run = await db.scalar(query)
    if run is None:
        raise ReconciliationConflict("原运行记录不存在。")
    if lock:
        await db.refresh(request, with_for_update=True)
    return run, request


async def _summary(
    db: AsyncSession,
    run: WorkflowExecution,
    request: WorkflowApprovalRequest,
) -> ReconciliationSummary:
    if not _pending(run, request):
        raise ReconciliationConflict("该运行已不再等待核验，请刷新记录。")
    ledger = _ledger(request)
    rows = await resume_receipts(db, run, request)
    key = settings.SECRET_KEY.get_secret_value() if settings.SECRET_KEY else ""
    if not key:
        raise ReconciliationConflict("核验摘要保管配置不可用。")
    payload = {
        "purpose": "approval-reconciliation:v1",
        "project": run.project_id,
        "execution": run.id,
        "approval": request.id,
        "definition": run.definition_snapshot,
        "input": run.input,
        "definition_digest": run.definition_digest,
        "summary": request.summary,
        "actor": request.approver_staff_id,
        "approved_checkpoint": request.checkpoint,
        "generation": ledger.generation,
        "progress": request.resume_checkpoint,
        "receipts": [
            {
                "id": row.id,
                "node_id": row.node_id,
                "type": row.node_type,
                "status": row.status,
                "started_at": row.started_at.isoformat(),
                "completed_at": row.completed_at.isoformat()
                if row.completed_at
                else None,
                "input": row.input,
                "output": row.output,
                "error": row.error,
            }
            for row in rows
        ],
    }
    try:
        frozen = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf8")
        digest = hmac.new(
            key.encode("utf8"), frozen, hashlib.sha256
        ).hexdigest()
    except (TypeError, ValueError, UnicodeError):
        raise ReconciliationConflict("核验内容无法生成稳定摘要。") from None
    reason: (
        Literal[
            "unknown_result",
            "progress_missing",
            "record_changed",
            "key_unavailable",
        ]
        | None
    )
    reason = None
    if not rows or any(row.status != "completed" for row in rows):
        reason = "unknown_result"
    elif request.resume_checkpoint is None:
        reason = "progress_missing"
    else:
        try:
            if (
                run.definition_snapshot is None
                or definition_digest(run.definition_snapshot)
                != run.definition_digest
            ):
                raise CheckpointCryptoError()
            await verified_resume_progress(db, run, request)
            if len(ledger.decisions) >= 255:
                raise CheckpointCryptoError()
        except ApprovalCheckpointKeyUnavailable:
            reason = "key_unavailable"
        except (CheckpointCryptoError, ValueError):
            reason = "record_changed"
    created = ledger.started_at or request.decided_at or request.requested_at
    definition = run.definition_snapshot or {}
    raw_nodes = definition.get("nodes", [])
    labels: dict[str, str] = {}
    if isinstance(raw_nodes, list):
        for node in raw_nodes:
            if not isinstance(node, dict):
                continue
            data = node.get("data")
            if isinstance(data, dict):
                identifier, label = node.get("id"), data.get("label")
                if isinstance(identifier, str) and isinstance(label, str):
                    labels[identifier] = label[:100]
    return ReconciliationSummary(
        id=request.id,
        execution_id=run.id,
        workflow_id=run.workflow_id,
        summary=request.summary,
        review_digest=digest,
        created_at=created,
        requested_at=request.requested_at,
        can_continue=reason is None,
        blocked_reason=reason,
        nodes=[
            ReconciliationNodeSummary(
                id=row.id,
                node_id=row.node_id,
                node_type=row.node_type,
                label=labels.get(row.node_id, ""),
                status=row.status,
                started_at=row.started_at,
                completed_at=row.completed_at,
            )
            for row in rows
        ],
    )


async def get_reconciliation(
    db: AsyncSession,
    project: str,
    actor: str,
    approval: str,
) -> ReconciliationSummary:
    run, request = await _pair(db, project, actor, approval)
    return await _summary(db, run, request)


async def list_reconciliations(
    db: AsyncSession,
    project: str,
    actor: str,
    *,
    limit: int = 30,
    before_requested_at: datetime | None = None,
    before_id: str | None = None,
) -> list[ReconciliationSummary]:
    if (before_requested_at is None) != (before_id is None):
        raise ReconciliationConflict("核验分页位置不完整。")
    query = (
        select(WorkflowApprovalRequest)
        .join(
            WorkflowExecution,
            WorkflowExecution.active_approval_id == WorkflowApprovalRequest.id,
        )
        .where(
            WorkflowApprovalRequest.project_id == project,
            WorkflowApprovalRequest.approver_staff_id == actor,
            WorkflowApprovalRequest.status == "approved",
            WorkflowExecution.project_id == project,
            WorkflowExecution.status == "running",
            WorkflowExecution.active_segment_started_at.is_(None),
            WorkflowExecution.error == RECONCILIATION_REQUIRED,
        )
    )
    if before_requested_at is not None and before_id is not None:
        query = query.where(
            or_(
                WorkflowApprovalRequest.requested_at < before_requested_at,
                and_(
                    WorkflowApprovalRequest.requested_at
                    == before_requested_at,
                    WorkflowApprovalRequest.id < before_id,
                ),
            )
        )
    rows = list(
        (
            await db.scalars(
                query.order_by(
                    WorkflowApprovalRequest.requested_at.desc(),
                    WorkflowApprovalRequest.id.desc(),
                ).limit(min(max(limit, 1), 100))
            )
        ).all()
    )
    results = []
    for request in rows:
        run = await db.get(WorkflowExecution, request.execution_id)
        if run is not None and _pending(run, request):
            results.append(await _summary(db, run, request))
    return results


async def decide_reconciliation(
    db: AsyncSession,
    project: str,
    actor: str,
    approval: str,
    decision: Literal["continue", "stop"],
    expected_digest: str,
) -> ReconciliationDecisionResult:
    if decision not in {"continue", "stop"}:
        raise ReconciliationConflict("核验处理方式无效。")
    run, request = await _pair(db, project, actor, approval, lock=True)
    ledger = _ledger(request)
    for previous in ledger.decisions:
        if hmac.compare_digest(previous.review_digest, expected_digest):
            if previous.decision != decision or previous.decided_by != actor:
                raise ReconciliationConflict("该核验已按其他结论处理。")
            return ReconciliationDecisionResult(
                id=request.id,
                execution_id=run.id,
                decision=decision,
                status=previous.status,
                decided_at=previous.decided_at,
            )
    summary = await _summary(db, run, request)
    if not hmac.compare_digest(summary.review_digest, expected_digest):
        raise ReconciliationConflict("核验内容已变化，请刷新后重新确认。")
    if decision == "continue" and not summary.can_continue:
        raise ReconciliationConflict("步骤结果尚未核实，不能继续执行。")
    now = datetime.now(UTC)
    status: Literal["pending_resume", "cancelled"]
    status = "pending_resume" if decision == "continue" else "cancelled"
    record = ReconciliationDecisionRecord(
        generation=ledger.generation,
        review_digest=expected_digest,
        decision=decision,
        decided_by=actor,
        decided_at=now,
        status=status,
        checkpoint_digest=progress_digest(request)
        if decision == "continue"
        else None,
    )
    ledger.decisions.append(record)
    request.reconciliation_ledger = ledger.model_dump(mode="json")
    run.status = status
    run.active_segment_started_at = None
    if decision == "stop":
        run.active_approval_id = None
        run.input = None
        run.output = None
        run.error = "原确认人已核验并停止本次流程；已发生的业务操作不作撤销。"
        run.completed_at = now
        await scrub_terminal_approval_checkpoints(db, project, run.id)
        await scrub_webhook_inputs(db, project, run.id)
    else:
        request.checkpoint = {
            **request.checkpoint,
            "capture_status": VERIFIED_RESUME_CAPTURE,
        }
        run.error = "已核验保存结果，等待继续剩余步骤。"
    await db.commit()
    return ReconciliationDecisionResult(
        id=request.id,
        execution_id=run.id,
        decision=decision,
        status=status,
        decided_at=now,
    )


async def get_reconciliation_decision(
    db: AsyncSession,
    project: str,
    actor: str,
    approval: str,
    digest: str,
) -> ReconciliationDecisionResult | None:
    run, request = await _pair(db, project, actor, approval)
    for record in _ledger(request).decisions:
        if hmac.compare_digest(record.review_digest, digest):
            return ReconciliationDecisionResult(
                id=request.id,
                execution_id=run.id,
                decision=record.decision,
                status=record.status,
                decided_at=record.decided_at,
            )
    return None
