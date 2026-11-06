"""Persist a successful step and its encrypted cursor in one transaction."""

import hashlib
import json
from datetime import UTC, datetime

from cryptography.fernet import InvalidToken
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.checkpoint import ExecutionCheckpoint
from app.models.approval import WorkflowApprovalRequest
from app.models.execution import NodeExecution, WorkflowExecution
from app.schemas.approval_reconciliation import ReconciliationLedger
from app.services.approval_checkpoint import (
    MAX_CHECKPOINT_BYTES,
    VERIFIED_RESUME_CAPTURE,
    CheckpointCryptoError,
    _cipher,
    _keyring,
    _scope,
    open_checkpoint,
)
from app.services.approval_resume_recovery import _owned_run
from app.services.approval_inputs import approval_input_digest


class ResumeProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkpoint: ExecutionCheckpoint
    receipt_ids: list[str] = Field(min_length=1, max_length=1024)
    receipt_digests: dict[str, str]


def receipt_digest(row: NodeExecution) -> str:
    def timestamp(value: datetime | None) -> str | None:
        if value is None:
            return None
        current = (
            value.replace(tzinfo=UTC)
            if value.tzinfo is None
            else value.astimezone(UTC)
        )
        return current.isoformat(timespec="microseconds")

    payload = {
        "id": row.id,
        "node_id": row.node_id,
        "type": row.node_type,
        "status": row.status,
        "input": row.input,
        "output": row.output,
        "error": row.error,
        "started_at": timestamp(row.started_at),
        "completed_at": timestamp(row.completed_at),
        "duration": row.duration,
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf8")
    ).hexdigest()


def progress_digest(request: WorkflowApprovalRequest) -> str | None:
    if request.resume_checkpoint is None:
        return None
    return hashlib.sha256(
        json.dumps(
            request.resume_checkpoint,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


def _verify_progress(
    run: WorkflowExecution,
    request: WorkflowApprovalRequest,
    saved: ResumeProgress,
) -> None:
    original = open_checkpoint(run, request)
    value = saved.checkpoint
    definition = run.definition_snapshot or {}
    nodes = definition.get("nodes", [])
    known = (
        {
            node["id"]
            for node in nodes
            if isinstance(node, dict) and isinstance(node.get("id"), str)
        }
        if isinstance(nodes, list)
        else set()
    )
    if (
        value.project_id != run.project_id
        or value.execution_id != run.id
        or value.definition_digest != run.definition_digest
        or value.input_digest != original.input_digest
        or value.workflow_lineage != original.workflow_lineage
        or value.completed_nodes[: len(original.completed_nodes)]
        != original.completed_nodes
        or len(value.completed_nodes) != len(set(value.completed_nodes))
        or len(saved.receipt_ids) != len(set(saved.receipt_ids))
        or set(saved.receipt_ids) != set(saved.receipt_digests)
        or not set(value.completed_nodes).issubset(known)
        or not set(value.current_nodes + value.next_nodes).issubset(known)
        or set(value.completed_nodes).intersection(
            value.current_nodes + value.next_nodes
        )
        or value.node_index <= len(value.completed_nodes)
        or any(
            value.context_data.get(key) != expected
            for key, expected in original.context_data.items()
        )
    ):
        raise CheckpointCryptoError("恢复进度与原审批不一致。")


def open_resume_progress(
    run: WorkflowExecution,
    request: WorkflowApprovalRequest,
) -> ResumeProgress:
    envelope = request.resume_checkpoint
    if not isinstance(envelope, dict) or set(envelope) != {
        "capture_status",
        "key_id",
        "ciphertext",
    }:
        raise CheckpointCryptoError("没有完整保存的恢复位置。")
    key_id, encrypted = envelope.get("key_id"), envelope.get("ciphertext")
    if (
        envelope.get("capture_status") != "encrypted"
        or not isinstance(key_id, str)
        or not 1 <= len(key_id) <= 32
        or not isinstance(encrypted, str)
        or len(encrypted) > 2 * MAX_CHECKPOINT_BYTES
    ):
        raise CheckpointCryptoError("恢复位置格式无效。")
    try:
        plaintext = _cipher(_keyring(), key_id).decrypt(
            encrypted.encode("ascii")
        )
        if len(plaintext) > MAX_CHECKPOINT_BYTES:
            raise ValueError()
        payload = json.loads(plaintext)
        if (
            not isinstance(payload, dict)
            or set(payload) != {"scope", "progress"}
            or payload["scope"]
            != {**_scope(run, request), "purpose": "resume-progress:v1"}
        ):
            raise ValueError()
        progress = ResumeProgress.model_validate(payload["progress"])
    except CheckpointCryptoError:
        raise
    except (InvalidToken, ValueError, TypeError, UnicodeError):
        raise CheckpointCryptoError("恢复位置无法解密或归属已变化。") from None
    _verify_progress(run, request, progress)
    return progress


async def resume_receipts(
    db: AsyncSession,
    run: WorkflowExecution,
    request: WorkflowApprovalRequest,
) -> list[NodeExecution]:
    if request.decided_at is None:
        raise CheckpointCryptoError("原审批决定无效。")
    return list(
        (
            await db.scalars(
                select(NodeExecution)
                .where(
                    NodeExecution.execution_id == run.id,
                    NodeExecution.project_id == run.project_id,
                    NodeExecution.started_at >= request.decided_at,
                )
                .order_by(NodeExecution.started_at, NodeExecution.id)
            )
        ).all()
    )


async def verified_resume_progress(
    db: AsyncSession,
    run: WorkflowExecution,
    request: WorkflowApprovalRequest,
) -> ExecutionCheckpoint:
    progress = open_resume_progress(run, request)
    if progress.checkpoint.input_digest != await approval_input_digest(
        db, run
    ):
        raise CheckpointCryptoError("原审批输入已变化。")
    rows = await resume_receipts(db, run, request)
    if (
        not rows
        or any(row.status != "completed" for row in rows)
        or set(progress.receipt_ids) != {row.id for row in rows}
        or not {row.node_id for row in rows}.issubset(
            progress.checkpoint.completed_nodes
        )
        or any(
            progress.receipt_digests[row.id] != receipt_digest(row)
            for row in rows
        )
    ):
        raise CheckpointCryptoError("步骤结果尚未完整保存，不能继续。")
    return progress.checkpoint


async def save_resume_progress(
    db: AsyncSession,
    project_id: str,
    execution_id: str,
    approval_id: str,
    segment: datetime | None,
    checkpoint: ExecutionCheckpoint,
) -> None:
    run = await _owned_run(db, project_id, execution_id, segment)
    request = await db.get(WorkflowApprovalRequest, approval_id)
    if (
        request is None
        or run.active_approval_id != approval_id
        or request.status != "approved"
    ):
        raise CheckpointCryptoError("原审批归属已变化。")
    rows = await resume_receipts(db, run, request)
    progress = ResumeProgress(
        checkpoint=checkpoint,
        receipt_ids=[row.id for row in rows],
        receipt_digests={row.id: receipt_digest(row) for row in rows},
    )
    _verify_progress(run, request, progress)
    if any(row.status != "completed" for row in rows):
        raise CheckpointCryptoError("步骤结果尚未完整保存。")
    payload = {
        "scope": {**_scope(run, request), "purpose": "resume-progress:v1"},
        "progress": progress.model_dump(mode="json"),
    }
    plaintext = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(plaintext) > MAX_CHECKPOINT_BYTES:
        raise CheckpointCryptoError("恢复位置超过允许大小。")
    ring = _keyring()
    key_id = ring.active_id or "legacy"
    request.resume_checkpoint = {
        "capture_status": "encrypted",
        "key_id": key_id,
        "ciphertext": _cipher(ring, key_id).encrypt(plaintext).decode("ascii"),
    }
    await db.commit()


async def selected_resume_checkpoint(
    db: AsyncSession,
    run: WorkflowExecution,
    request: WorkflowApprovalRequest,
    original: ExecutionCheckpoint,
) -> ExecutionCheckpoint:
    if request.resume_checkpoint is None:
        if request.checkpoint.get("capture_status") == VERIFIED_RESUME_CAPTURE:
            raise CheckpointCryptoError("已核验的恢复位置缺失，禁止重放原步骤。")
        return original
    if request.checkpoint.get("capture_status") != VERIFIED_RESUME_CAPTURE:
        raise CheckpointCryptoError("恢复位置尚未完成版本绑定。")
    ledger = ReconciliationLedger.model_validate(
        request.reconciliation_ledger or {}
    )
    decision = ledger.decisions[-1] if ledger.decisions else None
    if (
        decision is None
        or decision.decision != "continue"
        or decision.generation != ledger.generation
        or decision.decided_by != request.approver_staff_id
        or decision.checkpoint_digest != progress_digest(request)
    ):
        raise CheckpointCryptoError("恢复位置尚未由原确认人核验。")
    return await verified_resume_progress(db, run, request)
