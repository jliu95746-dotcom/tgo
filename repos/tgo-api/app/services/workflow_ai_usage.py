"""Workflow model quota ownership reconciled by the durable run outcome.

The caller must first obtain WorkflowAIContext from the owning service. This
module deliberately stores execution references, never questions or answers.
"""

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from pydantic import JsonValue, TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.exceptions import TGOAPIException
from app.models.ai_usage import AIUsageReservation
from app.models.staff import Staff
from app.schemas.workflow_ai_usage import (
    WorkflowAICallReceipt,
    WorkflowAICommand,
    WorkflowAIContext,
    WorkflowAIResult,
    WorkflowUsageOutcome,
    WorkflowUsageOwner,
    WorkflowUsageReceipt,
)
from app.services import ai_usage
from app.services.ai_usage_runtime import ReplyPermit
from app.services.company_email import utc
from app.services.company_membership import lock_company

_CALLS = TypeAdapter(dict[str, WorkflowAICallReceipt])
_MAX_CALLS = 64


@dataclass(frozen=True)
class WorkflowAIClaim:
    permit: ReplyPermit | None
    call_key: str
    request_digest: str
    replay_execution_id: UUID | None = None


def _error(message: str, *, forbidden: bool = False) -> TGOAPIException:
    return TGOAPIException(
        message,
        code="WORKFLOW_AI_OWNER_INVALID"
        if forbidden
        else "WORKFLOW_AI_PENDING",
        status_code=403 if forbidden else 409,
    )


def _calls(row: AIUsageReservation) -> dict[str, WorkflowAICallReceipt]:
    try:
        calls = _CALLS.validate_python(
            (row.receipt or {}).get("workflow_ai_calls", {})
        )
        if len(calls) > _MAX_CALLS:
            raise ValueError("Too many workflow calls")
        for key, call in calls.items():
            if key != _call_key(call.execution_id, call.node_id):
                raise ValueError("Invalid workflow call identity")
        return calls
    except (ValueError, ValidationError):
        raise _error("工作流用量记录无法确认，请查询原运行记录") from None


def _call_key(execution_id: UUID, node_id: str) -> str:
    return hashlib.sha256(f"{execution_id}:{node_id}".encode()).hexdigest()


def _save_calls(
    db: Session,
    row: AIUsageReservation,
    calls: dict[str, WorkflowAICallReceipt],
) -> None:
    row.receipt = {
        **(row.receipt or {}),
        "workflow_ai_calls": {
            key: value.model_dump(mode="json") for key, value in calls.items()
        },
    }
    db.commit()


def _require_context(
    command: WorkflowAICommand, context: WorkflowAIContext
) -> None:
    if (
        context.project_id != command.project_id
        or context.execution_id != command.execution_id
        or context.node_id != command.node_id
        or context.agent_id != command.payload.agent_id
        or context.status != "running"
        or context.expected_device_id != command.payload.expected_device_id
        or context.expected_device_policy_digest
        != command.payload.expected_device_policy_digest
    ):
        raise _error("工作流节点与当前运行范围不一致", forbidden=True)


def _require_staff(
    db: Session, project_id: UUID, owner: WorkflowUsageOwner
) -> None:
    if owner.staff_id is None:
        return
    staff = db.scalar(
        select(Staff)
        .where(Staff.id == owner.staff_id)
        .execution_options(populate_existing=True)
    )
    if (
        staff is None
        or staff.project_id != project_id
        or not staff.account_enabled
        or not staff.is_active
        or staff.deleted_at is not None
        or staff.role not in {"admin", "user"}
        or staff.token_version != owner.staff_token_version
    ):
        raise _error("原操作人员的企业权限已变更，请重新进入工作流", forbidden=True)


def _reservation(
    db: Session, context: WorkflowAIContext
) -> AIUsageReservation | None:
    owner = context.owner
    account = lock_company(db, context.project_id)
    ai_usage.require_service(account, datetime.now(timezone.utc))
    _require_staff(db, context.project_id, owner)
    if owner.unmetered:
        if account is not None:
            raise _error("企业工作流缺少额度归属，请重新从后台或原客户会话执行", forbidden=True)
        return None
    if owner.reservation_id is not None:
        assert owner.lease_id is not None
        return ai_usage.owned(
            db, context.project_id, owner.reservation_id, owner.lease_id
        )
    assert owner.staff_id is not None and owner.staff_token_version is not None
    if account is None:
        return None
    key = f"workflow:{context.execution_id}"
    row = db.scalar(
        select(AIUsageReservation)
        .where(
            AIUsageReservation.project_id == context.project_id,
            AIUsageReservation.round_key == key,
        )
        .execution_options(populate_existing=True)
    )
    receipt = WorkflowUsageReceipt(
        execution_id=context.execution_id,
        staff_id=owner.staff_id,
        staff_token_version=owner.staff_token_version,
    )
    if row is None:
        row = ai_usage.reserve(db, context.project_id, key)
        assert row is not None
        row.receipt = receipt.model_dump(mode="json")
    else:
        raw = {
            key: value
            for key, value in (row.receipt or {}).items()
            if key != "workflow_ai_calls"
        }
        try:
            previous = WorkflowUsageReceipt.model_validate(raw)
        except ValidationError:
            raise _error("原工作流用量归属无法确认") from None
        if previous != receipt:
            raise _error("原工作流的额度归属已变更", forbidden=True)
        if row.status in {"reserved", "review"} and (
            row.status == "review"
            or utc(row.expires_at) <= datetime.now(timezone.utc)
        ):
            calls = _calls(row)
            if calls and all(
                call.state in {"completed", "rejected"}
                for call in calls.values()
            ):
                running = (
                    db.scalar(
                        select(func.count())
                        .select_from(AIUsageReservation)
                        .where(
                            AIUsageReservation.project_id
                            == context.project_id,
                            AIUsageReservation.id != row.id,
                            AIUsageReservation.status.in_(
                                ["reserved", "publishing"]
                            ),
                            AIUsageReservation.expires_at
                            > datetime.now(timezone.utc),
                        )
                    )
                    or 0
                )
                if running >= settings.SAAS_AI_MAX_CONCURRENT_REPLIES:
                    raise TGOAPIException(
                        "AI 同时运行数量已达上限",
                        code="AI_CONCURRENCY_EXCEEDED",
                        status_code=429,
                    )
                row.status = "reserved"
                row.lease_id = uuid4()
                row.expires_at = datetime.now(timezone.utc) + timedelta(
                    minutes=15
                )
                db.flush()
    return row


def claim(
    command: WorkflowAICommand, context: WorkflowAIContext
) -> WorkflowAIClaim:
    """Claim before dispatch and serialize retries with the company lock."""
    _require_context(command, context)
    key = _call_key(command.execution_id, command.node_id)
    digest = hmac.new(
        settings.SECRET_KEY.encode(),
        json.dumps(
            command.model_dump(mode="json"),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode(),
        hashlib.sha256,
    ).hexdigest()
    with SessionLocal() as db:
        if not settings.SAAS_ENABLED or not settings.SAAS_BILLING_ENABLED:
            _require_staff(db, context.project_id, context.owner)
            return WorkflowAIClaim(None, key, digest)
        row = _reservation(db, context)
        if row is None:
            db.commit()
            return WorkflowAIClaim(None, key, digest)
        if row.status != "reserved" or utc(row.expires_at) <= datetime.now(
            timezone.utc
        ):
            raise _error("原额度授权已失效或结果待核验，请查询原运行记录")
        calls = _calls(row)
        permit = ReplyPermit(context.project_id, row.id, row.lease_id, False)
        previous = calls.get(key)
        if previous is not None:
            if previous.request_digest != digest:
                raise _error("同一工作流节点不能重复提交不同内容")
            if previous.state != "completed":
                raise _error("原 AI 调用已提交，请查询原运行记录，避免重复执行")
            db.commit()
            return WorkflowAIClaim(
                permit, key, digest, previous.agent_execution_id
            )
        if len(calls) >= _MAX_CALLS:
            raise _error("同一轮工作流的 AI 步骤已达上限")
        calls[key] = WorkflowAICallReceipt(
            execution_id=command.execution_id,
            node_id=command.node_id,
            request_digest=digest,
            state="claimed",
        )
        _save_calls(db, row, calls)
        return WorkflowAIClaim(permit, key, digest)


def _record(
    claimed: WorkflowAIClaim,
    state: str,
    result: WorkflowAIResult | None = None,
) -> None:
    permit = claimed.permit
    if permit is None:
        return
    with SessionLocal() as db:
        row = ai_usage.owned(
            db, permit.project_id, permit.reservation_id, permit.lease_id
        )
        calls = _calls(row)
        previous = calls.get(claimed.call_key)
        if (
            previous is None
            or previous.request_digest != claimed.request_digest
        ):
            raise _error("AI 回执与原工作流节点不一致")
        changed = previous.model_copy(
            update={
                "state": state,
                "agent_execution_id": result.execution_id
                if result is not None
                else None,
            }
        )
        changed = WorkflowAICallReceipt.model_validate(changed.model_dump())
        if previous.state in {"completed", "rejected"}:
            if previous != changed:
                raise _error("原 AI 调用已有不同的终态回执")
            return
        if row.status not in {"reserved", "review"}:
            raise _error("原额度已结束，不能接受迟到的 AI 回执")
        calls[claimed.call_key] = changed
        if (
            state == "unknown"
            and (row.receipt or {}).get("kind") == "workflow"
        ):
            row.status = "review"
            row.expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        _save_calls(db, row, calls)


def complete(claimed: WorkflowAIClaim, result: WorkflowAIResult) -> None:
    _record(claimed, "completed", result)


def reject(claimed: WorkflowAIClaim) -> None:
    """Only for explicit pre-generation rejection, never a transport error."""
    _record(claimed, "rejected")


def unknown(claimed: WorkflowAIClaim) -> None:
    _record(claimed, "unknown")


def _has_output(value: JsonValue | None) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_has_output(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_output(item) for item in value)
    return value is not None


def finish(permit: ReplyPermit | None, outcome: WorkflowUsageOutcome) -> None:
    """Only standalone workflows settle; customer replies own their quota."""
    if permit is None:
        return
    if permit.project_id != outcome.project_id:
        raise _error("工作流结果的企业归属不一致", forbidden=True)
    with SessionLocal() as db:
        row = ai_usage.owned(
            db, permit.project_id, permit.reservation_id, permit.lease_id
        )
        receipt = row.receipt or {}
        if receipt.get("kind") != "workflow":
            return
        if receipt.get("execution_id") != str(outcome.execution_id):
            raise _error("工作流结果与额度预约不一致", forbidden=True)
        calls = _calls(row)
        if row.status in {"settled", "released"}:
            return
        terminal = outcome.status in {
            "completed",
            "failed",
            "rejected",
            "expired",
            "cancelled",
        }
        if not terminal:
            if outcome.status in {
                "waiting_approval",
                "pending_resume",
            } and all(
                call.state in {"completed", "rejected"}
                for call in calls.values()
            ):
                row.status = "review"
                row.expires_at = datetime.now(timezone.utc) + timedelta(
                    minutes=5
                )
                db.commit()
            return
        if (
            outcome.answer_available
            or (outcome.status == "completed" and _has_output(outcome.output))
        ) and any(call.state == "completed" for call in calls.values()):
            row.status = "review"
            db.flush()
            ai_usage.settle(db, permit.project_id, row.id, row.lease_id)
        elif any(
            call.state in {"claimed", "unknown"} for call in calls.values()
        ):
            row.status = "review"
            row.expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        else:
            ai_usage.release(
                db,
                permit.project_id,
                row.id,
                row.lease_id,
                delivery_rejected=True,
            )
        db.commit()


def require_current_owner(db: Session, row: AIUsageReservation) -> None:
    """Recheck staff identity at the platform-model authorization gate."""
    if (row.receipt or {}).get("kind") != "workflow":
        return
    try:
        receipt = WorkflowUsageReceipt.model_validate(
            {
                key: value
                for key, value in (row.receipt or {}).items()
                if key != "workflow_ai_calls"
            }
        )
    except ValidationError:
        raise _error("工作流额度归属记录无效", forbidden=True) from None
    _require_staff(
        db,
        row.project_id,
        WorkflowUsageOwner(
            staff_id=receipt.staff_id,
            staff_token_version=receipt.staff_token_version,
        ),
    )
