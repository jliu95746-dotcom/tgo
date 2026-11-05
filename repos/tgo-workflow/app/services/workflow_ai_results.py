"""One original call per node; encrypted answers live only during recovery."""
from datetime import datetime, timedelta, timezone
import hmac
import json
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.execution import WorkflowExecution
from app.schemas.ai_usage import (
    WorkflowAICacheCommand,
    WorkflowAICacheReceipt,
    WorkflowAICacheWrite,
    WorkflowUsageOwner,
)
from app.services.approval_retention import TERMINAL_EXECUTION_STATUSES
from app.services.execution_snapshot import definition_digest
from app.services.workflow_ai_context import read_context
from app.services import workflow_ai_result_crypto as crypto

MAX_RECEIPT_BYTES = 2 * 1024 * 1024


class SavedCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    state: Literal["pending", "completed", "expired"]
    claim_id: UUID
    request_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    key_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")
    expires_at: datetime
    agent_execution_id: UUID | None = None
    ciphertext: str | None = Field(default=None, max_length=1024 * 1024)


class ResultEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[1] = 1
    calls: dict[str, SavedCall] = Field(default_factory=dict, max_length=64)


async def _lock_run(
    db: AsyncSession,
    request: WorkflowAICacheCommand,
) -> WorkflowExecution:
    command = request.command
    context = await read_context(
        db,
        command.project_id,
        command.execution_id,
        command.node_id,
    )
    payload = command.payload
    if (
        payload.agent_id != context.agent_id
        or payload.expected_device_id != context.expected_device_id
        or payload.expected_device_policy_digest
        != context.expected_device_policy_digest
    ):
        raise HTTPException(403, "原 AI 节点与当前执行范围不一致。")
    row = await db.scalar(
        select(WorkflowExecution)
        .where(
            WorkflowExecution.id == str(command.execution_id),
            WorkflowExecution.project_id == str(command.project_id),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None or row.status != "running":
        raise HTTPException(403, "原工作流已停止，不再恢复回答。")
    try:
        owner = WorkflowUsageOwner.model_validate(
            row.ai_usage_owner or {"unmetered": True},
        )
        if (
            owner != context.owner
            or row.definition_snapshot is None
            or definition_digest(row.definition_snapshot)
            != row.definition_digest
        ):
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(403, "原工作流范围已变化。") from None
    return row


def _load(run: WorkflowExecution) -> ResultEnvelope:
    if run.ai_result_receipts is None:
        return ResultEnvelope()
    try:
        return ResultEnvelope.model_validate(run.ai_result_receipts)
    except ValidationError:
        raise HTTPException(409, "原回答保存状态无法核验，禁止重新生成。") from None


def _base(request: WorkflowAICacheCommand) -> WorkflowAICacheCommand:
    return WorkflowAICacheCommand(
        command=request.command,
        reservation_id=request.reservation_id,
        lease_id=request.lease_id,
    )


def _require_same(
    run: WorkflowExecution,
    request: WorkflowAICacheCommand,
    saved: SavedCall,
) -> None:
    expected = crypto.fingerprint(
        run,
        _base(request),
        str(saved.claim_id),
        saved.expires_at,
        saved.key_id,
    )
    if not hmac.compare_digest(saved.request_digest, expected):
        raise HTTPException(409, "原回答与当前请求、归属或额度租约不一致。")
    if saved.state == "expired" or crypto.utc(
        saved.expires_at
    ) <= datetime.now(timezone.utc):
        raise HTTPException(410, "原回答恢复期限已结束，请查看原运行记录。")


def _receipt(saved: SavedCall) -> WorkflowAICacheReceipt:
    if saved.state == "pending":
        return WorkflowAICacheReceipt(state="pending", claim_id=saved.claim_id)
    if (
        saved.state != "completed"
        or not saved.ciphertext
        or saved.agent_execution_id is None
    ):
        raise HTTPException(409, "原回答尚未形成可核验回执。")
    result = crypto.unseal(
        saved.ciphertext, saved.request_digest, saved.key_id
    )
    if result.execution_id != saved.agent_execution_id:
        raise HTTPException(409, "原 AI 执行编号与回答不一致。")
    return WorkflowAICacheReceipt(
        state="completed",
        claim_id=saved.claim_id,
        result=result,
    )


async def _save(
    db: AsyncSession,
    run: WorkflowExecution,
    envelope: ResultEnvelope,
) -> None:
    data = envelope.model_dump(mode="json", exclude_none=True)
    if len(json.dumps(data, ensure_ascii=False).encode()) > MAX_RECEIPT_BYTES:
        raise HTTPException(413, "本轮回答恢复数据已达容量上限。")
    run.ai_result_receipts = data
    expiries = [
        crypto.utc(call.expires_at)
        for call in envelope.calls.values()
        if call.state != "expired"
    ]
    run.ai_result_expires_at = min(expiries) if expiries else None
    await db.commit()


async def prepare(
    db: AsyncSession,
    request: WorkflowAICacheCommand,
) -> WorkflowAICacheReceipt:
    run = await _lock_run(db, request)
    envelope = _load(run)
    saved = envelope.calls.get(request.command.node_id)
    if saved is not None:
        _require_same(run, request, saved)
        receipt = _receipt(saved)
        await db.commit()
        return receipt
    if len(envelope.calls) >= 64:
        raise HTTPException(409, "本轮 AI 节点调用已达上限。")
    claim_id = uuid4()
    key_id = crypto.active_key_id()
    expiry = datetime.now(timezone.utc) + timedelta(
        seconds=settings.WORKFLOW_AI_RESULT_TTL_SECONDS,
    )
    saved = SavedCall(
        state="pending",
        claim_id=claim_id,
        key_id=key_id,
        expires_at=expiry,
        request_digest=crypto.fingerprint(
            run, _base(request), str(claim_id), expiry, key_id
        ),
    )
    changed = ResultEnvelope(
        calls={**envelope.calls, request.command.node_id: saved}
    )
    await _save(db, run, changed)
    return WorkflowAICacheReceipt(state="claimed", claim_id=claim_id)


async def read(
    db: AsyncSession,
    request: WorkflowAICacheCommand,
) -> WorkflowAICacheReceipt:
    run = await _lock_run(db, request)
    saved = _load(run).calls.get(request.command.node_id)
    if saved is None:
        raise HTTPException(404, "没有原回答回执，不会自动重新生成。")
    _require_same(run, request, saved)
    receipt = _receipt(saved)
    await db.commit()
    return receipt


async def store(
    db: AsyncSession,
    request: WorkflowAICacheWrite,
) -> WorkflowAICacheReceipt:
    run = await _lock_run(db, request)
    envelope = _load(run)
    saved = envelope.calls.get(request.command.node_id)
    if saved is None or saved.claim_id != request.claim_id:
        raise HTTPException(409, "原回答缺少匹配的调用预约。")
    _require_same(run, request, saved)
    if saved.state == "completed":
        original = _receipt(saved)
        if original.result != request.result:
            raise HTTPException(409, "原调用已有不同的完成结果。")
        await db.commit()
        return original
    changed = saved.model_copy(
        update={
            "state": "completed",
            "agent_execution_id": request.result.execution_id,
            "ciphertext": crypto.seal(
                request.result, saved.request_digest, saved.key_id
            ),
        }
    )
    envelope = ResultEnvelope(
        calls={**envelope.calls, request.command.node_id: changed}
    )
    await _save(db, run, envelope)
    return WorkflowAICacheReceipt(
        state="completed",
        claim_id=changed.claim_id,
        result=request.result,
    )


async def purge(db: AsyncSession, *, now: datetime | None = None) -> int:
    """Keep replay tombstones and erase used or expired encrypted bodies."""
    current = crypto.utc(now or datetime.now(timezone.utc))
    rows = list(
        await db.scalars(
            select(WorkflowExecution)
            .where(
                WorkflowExecution.ai_result_expires_at.is_not(None),
                or_(
                    WorkflowExecution.ai_result_expires_at <= current,
                    WorkflowExecution.status.in_(TERMINAL_EXECUTION_STATUSES),
                ),
            )
            .order_by(WorkflowExecution.ai_result_expires_at)
            .limit(100)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
    )
    for run in rows:
        try:
            envelope = _load(run)
        except HTTPException:
            run.ai_result_receipts = {"state": "purged_invalid"}
            run.ai_result_expires_at = None
            continue
        terminal = run.status in TERMINAL_EXECUTION_STATUSES
        calls = {
            node: (
                saved.model_copy(
                    update={"state": "expired", "ciphertext": None}
                )
                if terminal or crypto.utc(saved.expires_at) <= current
                else saved
            )
            for node, saved in envelope.calls.items()
        }
        run.ai_result_receipts = ResultEnvelope(calls=calls).model_dump(
            mode="json", exclude_none=True
        )
        expires = [
            crypto.utc(call.expires_at)
            for call in calls.values()
            if call.state != "expired"
        ]
        run.ai_result_expires_at = min(expires) if expires else None
    await db.commit()
    return len(rows)
