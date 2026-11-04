"""Persist one fixed published run for each trusted internal event ID."""

import hmac
from typing import Literal
from datetime import datetime, timezone

from fastapi import HTTPException
from pydantic import JsonValue
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.execution import WorkflowExecution
from app.models.workflow import Workflow
from app.integrations.workflow_event_source_client import verify_event_source
from app.schemas.event_trigger import (
    WorkflowEventAccepted, WorkflowEventRequest, WorkflowEventTestContext,
    WorkflowEventSourceBinding, WorkflowEventSourceVerification,
)
from app.services.execution_snapshot import copy_json_object, definition_digest
from app.services.event_keyring import EventKeyringError
from app.services.event_identity import event_execution_id
from app.services.event_inputs import (
    EventInputKeyUnavailable, ensure_event_input_keys,
    frozen_event_inputs, seal_event_inputs,
)
from app.services.event_receipts import new_event_receipt, saved_event_receipt, ensure_event_receipt_keys
from app.services.execution_lifecycle import ExecutionStopped
from app.services.approval_retention import scrub_terminal_approval_checkpoints
from app.services.event_rate_limit import admit_event
from app.services.validation_service import ValidationService
from app.services.workflow_dispatch import (
    WorkflowInitialDispatchError, enqueue_pending_initial,
)
from app.services.workflow_releases import PublishedDefinition, production_definition, verify_execution_binding

EventRunState = Literal[
    "pending", "pending_resume", "waiting_approval", "running",
]


class EventSourceStopped(ExecutionStopped):
    def __init__(self, message: str, *, changed: bool):
        super().__init__(message)
        self.changed = changed


class EventSourceUnavailable(RuntimeError):
    """A background check cannot determine current authority yet."""


def _same_event(
    saved: WorkflowExecution, *, project_id: str, workflow_id: str,
    event_id: str, receipt_data: dict[str, JsonValue], legacy_secret: str,
) -> bool:
    return (
        saved.project_id == project_id
        and saved.workflow_id == workflow_id
        and saved.source_channel == "internal_event"
        and saved.source_message_id == event_id
        and isinstance(saved.invocation_input_hmac, str)
        and hmac.compare_digest(saved.invocation_input_hmac, _saved_receipt(
            receipt_data, legacy_secret, saved.id, saved.event_receipt_key_id,
        ))
    )


def _saved_receipt(
    data: dict[str, JsonValue], legacy_secret: str,
    execution_id: str, key_id: str | None,
) -> str:
    try:
        return saved_event_receipt(data, legacy_secret, execution_id, key_id)
    except EventKeyringError:
        raise HTTPException(503, "事件历史回执密钥不可用，请核查服务端配置。") from None


async def _saved_event(
    db: AsyncSession, execution_id: str, project_id: str,
) -> WorkflowExecution | None:
    return await db.scalar(select(WorkflowExecution).where(
        WorkflowExecution.id == execution_id,
        WorkflowExecution.project_id == project_id,
    ))


async def require_active_internal_event(
    db: AsyncSession, project_id: str, workflow_id: str, execution_id: str,
    *, expected_status: EventRunState = "running",
    defer_unavailable: bool = False,
) -> None:
    """Recheck the current release and source authority before another node."""
    status = await db.scalar(select(Workflow.status).where(
        Workflow.id == workflow_id, Workflow.project_id == project_id,
    ))
    if status != "active":
        await _stop_event(db, project_id, workflow_id, execution_id,
                          "工作流已停用，事件任务停止执行。", expected_status=expected_status)
    run = await db.scalar(select(WorkflowExecution).where(
        WorkflowExecution.id == execution_id,
        WorkflowExecution.project_id == project_id,
        WorkflowExecution.workflow_id == workflow_id,
    ).execution_options(populate_existing=True))
    if run is None or run.status != expected_status:
        raise ExecutionStopped("工作流事件已停止执行。")
    inputs = run.input
    has_binding = isinstance(inputs, dict) and "_event_source" in inputs
    if not has_binding and run.visitor_session_id is None:
        await db.commit()
        return  # Existing manual event tests do not claim business authority.
    try:
        if not isinstance(inputs, dict) or not has_binding:
            raise ValueError("missing source")
        binding = WorkflowEventSourceBinding.model_validate(inputs["_event_source"])
        if run.visitor_session_id != str(binding.session_id):
            raise ValueError("session mismatch")
        published = await production_definition(db, workflow_id, project_id)
        verify_execution_binding(published, run.release_id, run.definition_digest)
        if (run.release_id is None or run.definition_digest is None
                or published_event_type(published) != "staff.session.closed"):
            raise ValueError("invalid source release")
        query = WorkflowEventSourceVerification.model_validate({
            "project_id": project_id, "workflow_id": workflow_id,
            "event_type": "staff.session.closed", "release_id": run.release_id,
            "release_digest": run.definition_digest,
            "source_binding": binding.model_dump(mode="json"),
        })
        # Release the read transaction before contacting the authority service.
        await db.commit()
        await verify_event_source(query)
    except (PermissionError, HTTPException, ValueError):
        await _stop_event(db, project_id, workflow_id, execution_id,
                          "事件订阅、来源或固定发布版本已失效，任务停止执行。", expected_status=expected_status)
    except RuntimeError:
        if defer_unavailable:
            await db.rollback()
            raise EventSourceUnavailable(
                "事件来源暂时无法核验，保留等待下次巡检。"
            ) from None
        await _stop_event(db, project_id, workflow_id, execution_id,
                          "事件来源暂时无法核验，任务已停止，请核查后重新测试。",
                          failed=True, expected_status=expected_status)


async def _stop_event(
    db: AsyncSession, project_id: str, workflow_id: str, execution_id: str,
    message: str, *, failed: bool = False,
    expected_status: EventRunState = "running",
) -> None:
    changed = await db.scalar(update(WorkflowExecution).where(
        WorkflowExecution.id == execution_id,
        WorkflowExecution.project_id == project_id,
        WorkflowExecution.workflow_id == workflow_id,
        WorkflowExecution.status == expected_status,
    ).values(status="failed" if failed else "cancelled", input=None,
             output=None, error=message,
             completed_at=datetime.now(timezone.utc)).returning(WorkflowExecution.id))
    if changed is not None:
        await scrub_terminal_approval_checkpoints(db, project_id, execution_id)
    await db.commit()
    raise EventSourceStopped(message, changed=changed is not None)


async def prepare_internal_event_claim(
    db: AsyncSession, run: WorkflowExecution,
) -> bool:
    """Check revocation before opening a body or consuming an approved cursor."""
    if run.status not in {"pending", "pending_resume"}:
        return True
    expected: Literal["pending", "pending_resume"] = (
        "pending" if run.status == "pending" else "pending_resume"
    )
    await require_active_internal_event(
        db, run.project_id, run.workflow_id, run.id, expected_status=expected,
    )
    await db.refresh(run)
    try:
        frozen_event_inputs(run)
    except EventInputKeyUnavailable:
        await db.execute(update(WorkflowExecution).where(
            WorkflowExecution.id == run.id,
            WorkflowExecution.project_id == run.project_id,
            WorkflowExecution.workflow_id == run.workflow_id,
            WorkflowExecution.source_channel == "internal_event",
            WorkflowExecution.status == expected,
        ).values(error="事件历史保管密钥暂不可用，请恢复原密钥；流程保留等待恢复。"))
        await db.commit()
        return False
    except ValueError:
        await _stop_event(
            db, run.project_id, run.workflow_id, run.id,
            "事件冻结输入无效，任务已停止。", failed=True, expected_status=expected,
        )
    await db.commit()
    return True


def published_event_type(published: PublishedDefinition) -> str:
    errors = ValidationService.validate_workflow(published.definition)
    if errors:
        raise HTTPException(409, "已发布工作流未通过当前配置校验。")
    raw_nodes = published.definition.get("nodes")
    if not isinstance(raw_nodes, list):
        raise HTTPException(409, "已发布工作流触发节点无效。")
    triggers = [node for node in raw_nodes
                if isinstance(node, dict) and node.get("type") == "event"]
    data = triggers[0].get("data") if len(triggers) == 1 else None
    if not isinstance(data, dict) or not isinstance(data.get("event_type"), str):
        raise HTTPException(409, "请先发布包含事件触发节点的工作流。")
    return str(data["event_type"])


async def event_test_context(
    db: AsyncSession, workflow_id: str, project_id: str,
) -> WorkflowEventTestContext:
    try:
        ensure_event_receipt_keys()
    except EventKeyringError:
        raise HTTPException(503, "事件回执密钥配置不可用。") from None
    try:
        ensure_event_input_keys()
    except EventInputKeyUnavailable:
        raise HTTPException(503, "事件正文保管密钥配置不可用。") from None
    published = await production_definition(db, workflow_id, project_id)
    return WorkflowEventTestContext(
        workflow_id=workflow_id, event_type=published_event_type(published),
        release_id=published.release_id, version=published.version,
        release_digest=definition_digest(published.definition),
    )


async def accept_event(
    db: AsyncSession, workflow_id: str, request: WorkflowEventRequest,
    event_id: str, secret: str,
) -> WorkflowEventAccepted:
    if (request.source_binding is not None
            and event_id != f"source-{request.source_binding.delivery_id}"):
        raise HTTPException(409, "业务事件编号与来源投递记录不一致。")
    execution_id = event_execution_id(request.project_id, workflow_id, event_id)
    inputs = copy_json_object({"data": request.data})
    receipt_data: dict[str, JsonValue] = {"event_type": request.event_type, "data": request.data}
    if request.source_binding is not None:
        source = copy_json_object(request.source_binding.model_dump(mode="json"))
        inputs["_event_source"] = source
        receipt_data["source_binding"] = source
    # Preserve legacy receipts; a bound test must replay the same release too.
    if request.expected_release_id is not None:
        receipt_data["expected_release_id"] = request.expected_release_id
        receipt_data["expected_release_digest"] = request.expected_release_digest
    existing = await _saved_event(db, execution_id, request.project_id)
    if existing is not None:
        if not _same_event(existing, project_id=request.project_id,
                           workflow_id=workflow_id, event_id=event_id,
                           receipt_data=receipt_data, legacy_secret=secret):
            raise HTTPException(409, "事件编号已用于不同的工作流事件。")
        return WorkflowEventAccepted(execution_id=existing.id,
                                     status=existing.status, deduplicated=True)

    try:
        receipt_key_id, receipt = new_event_receipt(receipt_data, secret, execution_id)
    except EventKeyringError:
        raise HTTPException(503, "事件回执密钥不可用，请核查服务端配置。") from None

    published = await production_definition(db, workflow_id, request.project_id)
    verify_execution_binding(published, request.expected_release_id, request.expected_release_digest)
    if published_event_type(published) != request.event_type:
        raise HTTPException(409, "事件类型与已发布工作流不匹配。")
    if request.source_binding is not None:
        query = WorkflowEventSourceVerification.model_validate({
            "project_id": request.project_id, "workflow_id": workflow_id,
            "event_type": request.event_type, "release_id": published.release_id,
            "release_digest": definition_digest(published.definition),
            "source_binding": request.source_binding.model_dump(mode="json"),
        })
        try:
            await verify_event_source(query)
        except PermissionError:
            raise HTTPException(409, "工作流业务事件订阅或来源已失效。") from None
        except RuntimeError:
            raise HTTPException(503, "工作流业务事件来源暂时无法核验。") from None
    await admit_event(request.project_id, workflow_id, event_id)
    run = WorkflowExecution(
        id=execution_id, project_id=request.project_id, workflow_id=workflow_id,
        status="pending", input=inputs, invocation_input_hmac=receipt,
        event_receipt_key_id=receipt_key_id,
        definition_snapshot=copy_json_object(published.definition),
        workflow_version=published.version,
        definition_digest=definition_digest(published.definition),
        execution_mode="production", release_id=published.release_id,
        source_channel="internal_event", source_message_id=event_id,
        visitor_session_id=(str(request.source_binding.session_id)
                            if request.source_binding is not None else None),
        workflow_lineage=[workflow_id], started_at=datetime.now(timezone.utc),
    )
    try:
        run.input = seal_event_inputs(run, inputs)
    except EventInputKeyUnavailable:
        raise HTTPException(503, "事件正文保管密钥不可用。") from None
    except ValueError:
        raise HTTPException(409, "事件正文无法安全保管。") from None
    db.add(run)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await _saved_event(db, execution_id, request.project_id)
        if existing is None or not _same_event(
            existing, project_id=request.project_id, workflow_id=workflow_id,
            event_id=event_id, receipt_data=receipt_data, legacy_secret=secret,
        ):
            raise HTTPException(409, "事件编号已用于不同的工作流事件。") from None
        return WorkflowEventAccepted(execution_id=existing.id,
                                     status=existing.status, deduplicated=True)

    # The committed pending row is authoritative. A lost broker acknowledgement
    # remains pending and is recovered by the initial-dispatch sweep.
    try:
        await enqueue_pending_initial(run)
    except WorkflowInitialDispatchError:
        pass
    return WorkflowEventAccepted(execution_id=run.id, status="pending", deduplicated=False)
