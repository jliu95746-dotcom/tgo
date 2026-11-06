from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, and_, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError
from app.database import get_db, AsyncSessionLocal
from anyio import CancelScope
from collections.abc import AsyncIterator
from pydantic import JsonValue
from app.models.execution import WorkflowExecution, NodeExecution
from app.models.approval import WorkflowApprovalRequest
from app.models.workflow import Workflow
import asyncio
import hmac
from app.schemas.execution import (
    ExecutionStatus,
    WorkflowExecution as WorkflowExecutionSchema,
    WorkflowExecuteRequest,
    WorkflowExecutionOrigin,
    WorkflowSyncResponse,
    WorkflowExecutionCancelResponse,
    WorkflowStartedEvent,
    WorkflowStartedData,
    NodeStartedEvent,
    NodeStartedData,
    NodeFinishedEvent,
    NodeFinishedData,
    WorkflowFinishedEvent,
    WorkflowFinishedData,
)
from datetime import datetime, timezone
import time
from app.services.workflow_dispatch import (
    enqueue_workflow,
    revoke_queued_execution,
    WorkflowDispatchError,
)
from app.services.execution_lifecycle import (
    ExecutionStopped,
    cancel_running_execution,
    finish_running_execution,
    read_execution_status,
    require_running,
)
from app.services.parallel_node_receipts import (
    preserve_cancelled_parallel_node,
)
from app.engine.executor import WorkflowExecutor, public_execution_error
from app.services.workflow_service import WorkflowService
from app.services.validation_service import ValidationService
from app.services.execution_definition import execution_graph
from app.services.execution_snapshot import (
    copy_json_object,
    definition_digest,
    input_receipt_hmac,
    captured_execution_payload,
    SNAPSHOT_ERROR,
)
from app.services.approval_retention import scrub_terminal_approval_checkpoints
from app.services.webhook_lifecycle import scrub_webhook_inputs
from app.services.workflow_releases import (
    production_definition,
    verify_approval_dependencies,
    verify_execution_binding,
)
from app.services.workflow_inputs import WorkflowInputError, validate_inputs
from app.services.workflow_lineage import (
    extend_workflow_lineage,
    require_parent_execution,
)
from app.schemas.capabilities import ExecutionMode
from app.config import settings
from app.core.logging import logger
from app.integrations.ai_client import AIClient
from app.schemas.business_query import BusinessExecutionContext
from app.schemas.ai_usage import WorkflowUsageOwner
from app.services.workflow_ai_context import request_owner
from app.services.business_query_context import (
    business_query_record_inputs,
    require_business_query_context,
)
from app.services.employee_workflow_binding import (
    EmployeeWorkflowBinding,
    check_employee_binding,
    saved_employee_binding,
)
from typing import List, Literal
import uuid

router = APIRouter()

# --- Helper Methods ---


async def _create_execution_record(
    db: AsyncSession,
    workflow_id: str,
    project_id: str,
    inputs: dict[str, JsonValue],
    status: str = "running",
    *,
    workflow_definition: dict[str, JsonValue] | None = None,
    workflow_version: int | None = None,
    execution_mode: ExecutionMode = "draft_test",
    release_id: str | None = None,
    employee_agent_id: str | None = None,
    origin: WorkflowExecutionOrigin | None = None,
    workflow_lineage: tuple[str, ...] = (),
    execution_id: str | None = None,
    invocation_input_hmac: str | None = None,
    usage_owner: WorkflowUsageOwner | None = None,
) -> WorkflowExecution:
    if (
        origin is not None
        and origin.business_project_id is not None
        and str(origin.business_project_id) != project_id
    ):
        raise ValueError("运行来源项目与工作流项目不一致")
    execution_id = execution_id or str(uuid.uuid4())
    db_execution = WorkflowExecution(
        id=execution_id,
        project_id=project_id,
        workflow_id=workflow_id,
        status=status,
        input=copy_json_object(inputs),
        invocation_input_hmac=invocation_input_hmac,
        ai_usage_owner=(
            usage_owner.model_dump(mode="json") if usage_owner else None
        ),
        definition_snapshot=(
            copy_json_object(workflow_definition)
            if workflow_definition is not None
            else None
        ),
        workflow_version=workflow_version,
        execution_mode=execution_mode,
        release_id=release_id,
        employee_agent_id=employee_agent_id,
        ai_request_id=origin.ai_request_id if origin is not None else None,
        business_request_id=(
            str(origin.business_request_id)
            if origin is not None and origin.business_request_id is not None
            else None
        ),
        visitor_session_id=(
            str(origin.visitor_session_id)
            if origin is not None and origin.visitor_session_id is not None
            else None
        ),
        source_message_id=(
            origin.source_message_id if origin is not None else None
        ),
        source_channel=origin.source_channel if origin is not None else None,
        parent_execution_id=(
            str(origin.parent_execution_id)
            if origin is not None and origin.parent_execution_id is not None
            else None
        ),
        workflow_lineage=list(workflow_lineage or (workflow_id,)),
        definition_digest=(
            definition_digest(workflow_definition)
            if workflow_definition is not None
            else None
        ),
        started_at=datetime.now(timezone.utc),
    )
    db.add(db_execution)
    await db.commit()
    return db_execution


async def _accepted_execution_payload(
    db: AsyncSession,
    run: WorkflowExecution,
    project_id: str,
) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    await db.refresh(
        run,
        attribute_names=["input", "definition_snapshot", "definition_digest"],
    )
    try:
        return captured_execution_payload(run)
    except RuntimeError:
        await finish_running_execution(
            db,
            run.id,
            project_id,
            "failed",
            None,
            SNAPSHOT_ERROR,
            0,
        )
        raise HTTPException(409, SNAPSHOT_ERROR) from None


def _same_approval_invocation(
    saved: WorkflowExecution,
    *,
    workflow_id: str,
    employee_agent_id: str | None,
    inputs: dict[str, JsonValue],
    release_id: str | None,
    workflow_version: int,
    definition: dict[str, JsonValue],
    origin: WorkflowExecutionOrigin | None,
    workflow_lineage: tuple[str, ...],
    usage_owner: WorkflowUsageOwner | None = None,
) -> bool:
    """
    A reused invocation key may only refer to the identical approved request.
    """
    if employee_agent_id is None or origin is None:
        return False
    if saved.input is None:
        key = settings.SECRET_KEY
        same_inputs = bool(
            key is not None
            and key.get_secret_value()
            and saved.invocation_input_hmac
            and hmac.compare_digest(
                saved.invocation_input_hmac,
                input_receipt_hmac(inputs, key.get_secret_value(), saved.id),
            )
        )
    else:
        same_inputs = saved.input == inputs
    return (
        saved.workflow_id == workflow_id
        and saved.employee_agent_id == employee_agent_id
        and saved.execution_mode == "production"
        and same_inputs
        and saved.release_id == release_id
        and saved.workflow_version == workflow_version
        and saved.definition_digest == definition_digest(definition)
        and saved.ai_request_id == origin.ai_request_id
        and saved.business_request_id
        == (
            str(origin.business_request_id)
            if origin.business_request_id
            else None
        )
        and saved.visitor_session_id
        == (
            str(origin.visitor_session_id)
            if origin.visitor_session_id
            else None
        )
        and saved.source_message_id == origin.source_message_id
        and saved.source_channel == origin.source_channel
        and saved.parent_execution_id
        == (
            str(origin.parent_execution_id)
            if origin.parent_execution_id
            else None
        )
        and saved.workflow_lineage == list(workflow_lineage)
        and saved.ai_usage_owner
        == (usage_owner.model_dump(mode="json") if usage_owner else None)
    )


async def _load_approval_invocation(
    db: AsyncSession,
    execution_id: str,
    project_id: str,
) -> WorkflowExecution | None:
    return await db.scalar(
        select(WorkflowExecution)
        .options(selectinload(WorkflowExecution.node_executions))
        .where(
            WorkflowExecution.id == execution_id,
            WorkflowExecution.project_id == project_id,
        )
        .execution_options(populate_existing=True)
    )


async def _run_stream_execution(
    workflow_id: str,
    project_id: str,
    execution_id: str,
    inputs: dict[str, JsonValue],
    workflow_definition: dict[str, JsonValue],
    started_at: datetime,
    db: AsyncSession,
    *,
    employee_binding: EmployeeWorkflowBinding | None = None,
    workflow_lineage: tuple[str, ...] = (),
):
    start_time = time.time()
    task_id = f"stream-{execution_id}"
    node_count = 0

    # 1. Emit workflow_started
    started_event = WorkflowStartedEvent(
        event="workflow_started",
        workflow_run_id=execution_id,
        task_id=task_id,
        data=WorkflowStartedData(
            id=execution_id,
            workflow_id=workflow_id,
            inputs=inputs,
            created_at=int(started_at.timestamp()),
        ),
    )
    yield f"data: {started_event.model_dump_json()}\n\n"

    async def check_active() -> None:
        await require_running(db, execution_id, project_id)
        await check_employee_binding(employee_binding, db)

    executor = WorkflowExecutor(
        workflow_definition,
        project_id=project_id,
        execution_id=execution_id,
        workflow_lineage=workflow_lineage or (workflow_id,),
        check_active=check_active,
    )
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    # Re-defining callbacks to use the queue
    async def q_on_node_start(node_id, node_type, node_data, index):
        await check_active()
        nonlocal node_count
        node_count += 1
        event = NodeStartedEvent(
            event="node_started",
            workflow_run_id=execution_id,
            task_id=task_id,
            data=NodeStartedData(
                id=str(uuid.uuid4()),
                node_id=node_id,
                node_type=node_type,
                title=node_data.get("title", node_type),
                index=index,
                created_at=int(time.time()),
            ),
        )
        await queue.put(f"data: {event.model_dump_json()}\n\n")

    async def q_on_node_complete(
        node_id,
        node_type,
        status,
        input,
        output,
        error,
        duration,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ):
        if status == "cancelled" and await preserve_cancelled_parallel_node(
            db,
            project_id=project_id,
            execution_id=execution_id,
            node_id=node_id,
            node_type=node_type,
            started_at=started_at,
            completed_at=completed_at,
            duration=duration,
        ):
            return
        await require_running(db, execution_id, project_id)
        await check_employee_binding(employee_binding, db)
        # Save to DB
        node_exec_id = str(uuid.uuid4())
        node_exec = NodeExecution(
            id=node_exec_id,
            execution_id=execution_id,
            project_id=project_id,
            node_id=node_id,
            node_type=node_type,
            status=status,
            input=input,
            output=output,
            error=error,
            duration=duration,
            started_at=started_at or datetime.now(timezone.utc),
            completed_at=completed_at,
        )
        db.add(node_exec)
        await db.commit()

        event = NodeFinishedEvent(
            event="node_finished",
            workflow_run_id=execution_id,
            task_id=task_id,
            data=NodeFinishedData(
                id=node_exec_id,
                node_id=node_id,
                node_type=node_type,
                inputs=input,
                outputs=output,
                status="succeeded" if status == "completed" else "failed",
                error=error,
                elapsed_time=duration / 1000.0,
                finished_at=int(time.time()),
            ),
        )
        await queue.put(f"data: {event.model_dump_json()}\n\n")

    status: Literal["completed", "failed", "cancelled"] = "completed"
    error_msg = None
    final_output = None

    executor_task: asyncio.Task[None] | None = None
    try:
        # Run executor in a separate task
        async def run_executor():
            nonlocal final_output, status, error_msg
            try:
                final_output = await executor.run(
                    inputs,
                    on_node_start=q_on_node_start,
                    on_node_complete=q_on_node_complete,
                )
                await check_employee_binding(employee_binding, db)
            except ExecutionStopped:
                status = "cancelled"
                final_output = None
            except Exception as e:
                status = "failed"
                error_msg = public_execution_error(e)
                logger.error(
                    "Workflow stream execution error: %s", type(e).__name__
                )
            finally:
                await queue.put(None)  # Signal end

        executor_task = asyncio.create_task(run_executor())

        while True:
            item = await queue.get()
            if item is None:
                break
            yield item

    except (asyncio.CancelledError, GeneratorExit):
        # Starlette cancels the stream task on disconnect. Finish cleanup in a
        # shield so the executor cannot outlive its database session.
        with CancelScope(shield=True):
            if executor_task is not None:
                executor_task.cancel()
                await asyncio.gather(executor_task, return_exceptions=True)
            await db.rollback()
            await cancel_running_execution(db, execution_id, project_id)
        raise
    except Exception as e:
        status = "failed"
        error_msg = public_execution_error(e)
    finally:
        with CancelScope(shield=True):
            if executor_task is not None:
                if not executor_task.done():
                    executor_task.cancel()
                await asyncio.gather(executor_task, return_exceptions=True)

    # Update WorkflowExecution in DB
    duration_ms = int((time.time() - start_time) * 1000)
    if status != "cancelled":
        changed = await finish_running_execution(
            db,
            execution_id,
            project_id,
            status,
            final_output,
            error_msg,
            duration_ms,
        )
        if not changed:
            saved_status = await read_execution_status(
                db,
                execution_id,
                project_id,
            )
            status = "cancelled" if saved_status == "cancelled" else "failed"
            final_output = None
    if status == "cancelled":
        error_msg = None

    # Emit workflow_finished
    finished_event = WorkflowFinishedEvent(
        event="workflow_finished",
        workflow_run_id=execution_id,
        task_id=task_id,
        data=WorkflowFinishedData(
            id=execution_id,
            workflow_id=workflow_id,
            status=(
                "succeeded"
                if status == "completed"
                else ("cancelled" if status == "cancelled" else "failed")
            ),
            outputs=final_output if final_output is not None else {},
            error=error_msg,
            elapsed_time=duration_ms / 1000.0,
            total_steps=node_count,
            finished_at=int(time.time()),
        ),
    )
    yield f"data: {finished_event.model_dump_json()}\n\n"


async def _stream_with_owned_session(
    workflow_id: str,
    project_id: str,
    execution_id: str,
    inputs: dict[str, JsonValue],
    workflow_definition: dict[str, JsonValue],
    started_at: datetime,
    *,
    employee_binding: EmployeeWorkflowBinding | None = None,
    workflow_lineage: tuple[str, ...] = (),
) -> AsyncIterator[str]:
    # Response iteration may outlive the request dependency's DB session.
    db = AsyncSessionLocal()
    stream = _run_stream_execution(
        workflow_id,
        project_id,
        execution_id,
        inputs,
        workflow_definition,
        started_at,
        db,
        employee_binding=employee_binding,
        workflow_lineage=workflow_lineage,
    )
    exhausted = False
    try:
        async for frame in stream:
            yield frame
        exhausted = True
    finally:
        with CancelScope(shield=True):
            try:
                await stream.aclose()
                await db.rollback()
                if not exhausted:
                    await cancel_running_execution(
                        db, execution_id, project_id
                    )
            finally:
                await db.close()


async def _run_sync_execution(
    workflow_id: str,
    project_id: str,
    execution_id: str,
    inputs: dict,
    workflow_definition: dict,
    started_at: datetime,
    db: AsyncSession,
    *,
    employee_binding: EmployeeWorkflowBinding | None = None,
    workflow_lineage: tuple[str, ...] = (),
    employee_agent_id: str | None = None,
    business_context: BusinessExecutionContext | None = None,
):
    perf_start = time.time()

    async def check_active() -> None:
        await require_running(db, execution_id, project_id)
        await check_employee_binding(employee_binding, db)

    executor = WorkflowExecutor(
        workflow_definition,
        project_id=project_id,
        execution_id=execution_id,
        workflow_lineage=workflow_lineage or (workflow_id,),
        employee_agent_id=employee_agent_id,
        business_context=business_context,
        check_active=check_active,
    )

    async def sync_on_node_start(node_id, node_type, node_data, index):
        await check_active()

    # 定义同步模式下的回调（保存到 DB）
    async def sync_on_node_complete(
        node_id,
        node_type,
        status,
        input,
        output,
        error,
        duration,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ):
        if status == "cancelled" and await preserve_cancelled_parallel_node(
            db,
            project_id=project_id,
            execution_id=execution_id,
            node_id=node_id,
            node_type=node_type,
            started_at=started_at,
            completed_at=completed_at,
            duration=duration,
        ):
            return
        await require_running(db, execution_id, project_id)
        await check_employee_binding(employee_binding, db)
        node_exec = NodeExecution(
            id=str(uuid.uuid4()),
            execution_id=execution_id,
            project_id=project_id,
            node_id=node_id,
            node_type=node_type,
            status=status,
            input=input,
            output=output,
            error=error,
            duration=duration,
            started_at=started_at or datetime.now(timezone.utc),
            completed_at=completed_at,
        )
        db.add(node_exec)
        await db.commit()

    success = True
    final_output = None
    error_msg = None
    try:
        final_output = await executor.run(
            inputs,
            on_node_start=sync_on_node_start,
            on_node_complete=sync_on_node_complete,
        )
        await check_employee_binding(employee_binding, db)
    except ExecutionStopped:
        success = False
    except Exception as e:
        success = False
        error_msg = public_execution_error(e)
        logger.error("Workflow sync execution error: %s", type(e).__name__)

    perf_duration = time.time() - perf_start
    end_time_dt = datetime.now(timezone.utc)

    # 更新最终状态
    changed = await finish_running_execution(
        db,
        execution_id,
        project_id,
        "completed" if success else "failed",
        final_output,
        error_msg,
        int(perf_duration * 1000),
    )
    if not changed:
        success = False
        final_output = None

    return {
        "success": success,
        "output": final_output if final_output is not None else {},
        "metadata": {
            "duration": round(perf_duration, 3),
            "startTime": started_at.isoformat(),
            "endTime": end_time_dt.isoformat(),
        },
    }


# --- Routes ---


@router.post(
    "/{workflow_id}/execute",
    response_model=WorkflowExecutionSchema | WorkflowSyncResponse,
    summary="Execute Workflow",
    description="""
                Execute a workflow using one of three supported modes:

                1. **Synchronous Mode (Default, `stream=False`,
                `async=False`)**:
                   Executes the workflow immediately and returns the final
                   output. Ideal for short-lived tasks.

                2. **Asynchronous Mode (`async=True`)**:
                   Creates an execution record and triggers a background Celery
                   task. Returns the execution record immediately.

                3. **Streaming Mode (`stream=True`)**:
                   Returns a Server-Sent Events (SSE) stream, pushing real-time
                   events as the workflow progresses.

                ### Streaming Mode Events (SSE):
                When `stream=True`, the response header includes `Content-Type:
                text/event-stream`.
                Each message starts with `data: ` followed by a JSON string and
                ends with `\n\n`.

                - **workflow_started**: Workflow execution initialized.
                - **node_started**: A specific node has started executing.
                - **node_finished**: A specific node has finished (success or
                failure).
                - **workflow_finished**: Entire workflow has finished.
                """,
    responses={
        200: {
            "description": "Successful Response",
            "content": {
                "application/json": {
                    "schema": {
                        "oneOf": [
                            {
                                "$ref": (
                                    "#/components/schemas/WorkflowSyncResponse"
                                )
                            },
                            {"$ref": "#/components/schemas/WorkflowExecution"},
                        ]
                    },
                    "examples": {
                        "sync_mode": {
                            "summary": "Synchronous Mode (Default)",
                            "description": (
                                "Returns final output and metadata."
                            ),
                            "value": {
                                "success": True,
                                "output": {
                                    "answer": "Paris is the capital of France."
                                },
                                "metadata": {
                                    "duration": 1.234,
                                    "startTime": "2026-01-03T06:16:44.478Z",
                                    "endTime": "2026-01-03T06:16:45.712Z",
                                },
                            },
                        },
                        "async_mode": {
                            "summary": "Asynchronous Mode (async=true)",
                            "description": (
                                "Returns the execution record with 'pending'"
                                " status."
                            ),
                            "value": {
                                "id": "d73d5f18-3cc0-400f-ab18-c0a5f05625f5",
                                "project_id": "proj-123",
                                "workflow_id": "wf-456",
                                "status": "pending",
                                "input": {
                                    "query": "What is the capital of France?"
                                },
                                "output": None,
                                "error": None,
                                "started_at": "2026-01-03T06:16:44.478Z",
                                "completed_at": None,
                                "duration": None,
                                "node_executions": [],
                            },
                        },
                    },
                },
                "text/event-stream": {
                    "description": "Streaming Mode (stream=true)",
                    "example": (
                        'data: {"event": "workflow_started",'
                        ' "workflow_run_id": "uuid-1", "task_id":'
                        ' "stream-uuid-1", "data": {"id": "uuid-1",'
                        ' "workflow_id": "wf-1", "inputs": {}, "created_at":'
                        ' 1735790000}}\n\ndata: {"event": "node_started",'
                        ' "workflow_run_id": "uuid-1", "task_id":'
                        ' "stream-uuid-1", "data": {"id": "node-exec-1",'
                        ' "node_id": "node-1", "node_type": "llm", "title":'
                        ' "AI Chat", "index": 1, "created_at":'
                        ' 1735790001}}\n\ndata: {"event": "node_finished",'
                        ' "workflow_run_id": "uuid-1", "task_id":'
                        ' "stream-uuid-1", "data": {"id": "node-exec-1",'
                        ' "node_id": "node-1", "node_type": "llm", "inputs":'
                        ' {}, "outputs": {"text": "Hello"}, "status":'
                        ' "succeeded", "error": null, "elapsed_time": 0.5,'
                        ' "finished_at": 1735790002}}\n\ndata: {"event":'
                        ' "workflow_finished", "workflow_run_id": "uuid-1",'
                        ' "task_id": "stream-uuid-1", "data": {"id": "uuid-1",'
                        ' "workflow_id": "wf-1", "status": "succeeded",'
                        ' "outputs": {"result": "Hello"}, "error": null,'
                        ' "elapsed_time": 0.6, "total_steps": 1,'
                        ' "finished_at": 1735790002}}\n\n'
                    ),
                },
            },
        },
        404: {"description": "Workflow not found"},
    },
)
async def execute_workflow(
    workflow_id: str,
    request: WorkflowExecuteRequest,
    project_id: str = Query(..., description="Project ID"),
    db: AsyncSession = Depends(get_db),
    x_internal_api_key: str | None = Header(default=None),
    x_business_request_context: str | None = Header(default=None),
    x_business_visitor_id: str | None = Header(default=None),
    x_business_request_fingerprint: str | None = Header(default=None),
    x_execution_channel: str | None = Header(default=None),
    x_workflow_staff_id: str | None = Header(default=None),
    x_workflow_staff_version: str | None = Header(default=None),
    x_ai_reservation: str | None = Header(default=None),
    x_ai_lease: str | None = Header(default=None),
):
    usage_owner = request_owner(
        x_internal_api_key,
        x_workflow_staff_id,
        x_workflow_staff_version,
        x_ai_reservation,
        x_ai_lease,
    )
    if usage_owner is not None and (
        (
            usage_owner.staff_id is not None
            and request.employee_agent_id is not None
        )
        or (
            usage_owner.reservation_id is not None
            and request.employee_agent_id is None
        )
    ):
        raise HTTPException(403, "工作流调用方与额度来源不一致")
    # 1. Verify workflow exists and belongs to project
    workflow = await WorkflowService.get_by_id(db, workflow_id, project_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    selected_definition = workflow.definition
    selected_version = workflow.version
    release_id = None
    if request.execution_mode == "production":
        if workflow.status != "active":
            raise HTTPException(
                status_code=409, detail="请先发布工作流，再用于正式业务执行。"
            )
        published = await production_definition(
            db, workflow_id, project_id, request.release_version
        )
        verify_execution_binding(
            published,
            request.expected_release_id,
            request.expected_release_digest,
        )
        selected_definition, selected_version, release_id = (
            published.definition,
            published.version,
            published.release_id,
        )
        published_nodes = selected_definition.get("nodes")
        if isinstance(published_nodes, list) and any(
            isinstance(node, dict) and node.get("type") == "timer"
            for node in published_nodes
        ):
            raise HTTPException(
                403, "定时工作流必须通过已登记计划派发，不能直接正式执行。"
            )
        if isinstance(published_nodes, list) and any(
            isinstance(node, dict) and node.get("type") == "webhook"
            for node in published_nodes
        ):
            raise HTTPException(
                403, "外部通知工作流必须通过已签名通知派发，不能直接正式执行。"
            )
    elif (
        request.release_version is not None
        or request.expected_release_id is not None
        or request.expected_release_digest is not None
    ):
        raise HTTPException(422, "指定发布版本仅用于正式业务执行。")

    errors = ValidationService.validate_workflow(selected_definition)
    if errors:
        raise HTTPException(
            status_code=400,
            detail={"message": "Workflow validation failed", "errors": errors},
        )
    selected_nodes = execution_graph(selected_definition).nodes
    if any(node.get("type") == "event" for node in selected_nodes):
        raise HTTPException(
            403, "事件工作流只能通过已签名的内部事件入口执行。"
        )

    has_approval = any(
        node.get("type") == "approval" for node in selected_nodes
    )
    if has_approval:
        if request.stream or not request.async_mode:
            raise HTTPException(
                422,
                "人工审批流程必须使用异步执行，等待期间通过运行记录查询状态。",
            )
        await verify_approval_dependencies(selected_definition, project_id)

    has_aftersales_review = any(
        node.get("type") == "approval"
        and isinstance(node.get("data"), dict)
        and node["data"].get("after_approval_action")
        == "create_aftersales_review"
        for node in selected_nodes
    )
    if has_aftersales_review and (
        request.execution_mode != "production"
        or request.employee_agent_id is None
        or request.origin is None
        or request.origin.business_request_id is None
        or request.origin.visitor_session_id is None
    ):
        raise HTTPException(
            403, "售后核对任务仅允许已核验的正式员工客户会话创建。"
        )

    has_business_query = any(
        node.get("type") == "business_query" for node in selected_nodes
    )
    if has_business_query and (request.stream or request.async_mode):
        raise HTTPException(
            422, "受控业务查询仅支持同步执行，客户会话凭证不得入队或持久化"
        )

    try:
        validated_inputs = validate_inputs(selected_definition, request.inputs)
    except WorkflowInputError as error:
        raise HTTPException(
            422,
            detail={
                "message": str(error),
                "errors": [issue.model_dump() for issue in error.issues],
            },
        ) from None
    if has_business_query:
        try:
            business_query_record_inputs(validated_inputs)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    if (
        isinstance(x_internal_api_key, str)
        and x_internal_api_key
        and request.employee_agent_id is None
        and usage_owner is None
    ):
        raise HTTPException(422, "内部员工工作流调用必须指定员工身份")

    workflow_lineage: tuple[str, ...] = (workflow_id,)
    execution_origin: WorkflowExecutionOrigin | None = None
    if request.employee_agent_id is not None:
        release_version = request.release_version
        release_id = request.expected_release_id
        release_digest = request.expected_release_digest
        if (
            release_version is None
            or release_id is None
            or release_digest is None
        ):
            raise HTTPException(
                422, "员工工作流执行必须指定完整的固定发布版本"
            )
        key = settings.SECRET_KEY
        if key is None or not key.get_secret_value():
            raise HTTPException(503, "员工工作流内部核验未配置")
        if not x_internal_api_key or not hmac.compare_digest(
            x_internal_api_key, key.get_secret_value()
        ):
            raise HTTPException(403, "员工工作流执行身份无法确认")
        if (
            request.origin is not None
            and request.origin.business_project_id is not None
            and str(request.origin.business_project_id) != project_id
        ):
            raise HTTPException(403, "客户业务请求来源与工作流项目不一致")
        try:
            execution_origin = await require_parent_execution(
                db, request.origin, project_id
            )
        except PermissionError as error:
            raise HTTPException(403, str(error)) from None
        try:
            workflow_lineage = extend_workflow_lineage(
                (
                    request.origin.workflow_lineage
                    if request.origin is not None
                    else ()
                ),
                workflow_id,
            )
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        # A private AI call can take time. Release the read transaction and
        # check the workflow's current switch again after the private hop.
        await db.commit()
        try:
            await AIClient.verify_employee_workflow_binding(
                project_id,
                str(request.employee_agent_id),
                workflow_id,
                release_version,
                release_id,
                release_digest,
            )
        except PermissionError:
            raise HTTPException(
                409, "员工工作流授权已变更，停止执行"
            ) from None
        except RuntimeError:
            raise HTTPException(503, "员工工作流授权暂时无法核验") from None
        current_status = await db.scalar(
            select(Workflow.status).where(
                Workflow.id == workflow_id,
                Workflow.project_id == project_id,
            )
        )
        if current_status != "active":
            raise HTTPException(409, "工作流已停用，停止执行")
        try:
            execution_origin = await require_parent_execution(
                db,
                execution_origin,
                project_id,
            )
        except PermissionError as error:
            raise HTTPException(403, str(error)) from None

    employee_agent_id = (
        str(request.employee_agent_id)
        if request.employee_agent_id is not None
        else None
    )
    if request.employee_agent_id is None:
        execution_origin = None
    approval_invocation_id: str | None = None
    if has_approval and employee_agent_id is not None:
        if request.invocation_key is None or execution_origin is None:
            raise HTTPException(
                422, "员工审批工作流必须提供运行来源和防重复调用标识"
            )
        approval_invocation_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"workflow-approval:{project_id}:{employee_agent_id}:"
                f"{workflow_id}:{request.invocation_key}",
            )
        )
    elif request.invocation_key is not None:
        raise HTTPException(422, "防重复调用标识仅用于员工审批工作流")
    business_context = None
    if has_business_query:
        if (
            request.employee_agent_id is None
            or request.execution_mode != "production"
        ):
            raise HTTPException(403, "受控业务查询只允许正式员工会话执行")
        try:
            business_context = require_business_query_context(
                project_id,
                execution_origin,
                context_token=x_business_request_context,
                visitor_id=x_business_visitor_id,
                request_fingerprint=x_business_request_fingerprint,
                channel=x_execution_channel,
            )
        except PermissionError as error:
            raise HTTPException(403, str(error)) from None
    employee_binding = saved_employee_binding(
        project_id,
        employee_agent_id,
        workflow_id,
        request.execution_mode,
        selected_version,
        release_id,
        definition_digest(selected_definition),
    )

    if request.stream:
        # --- 1. 流式执行模式 (SSE) ---
        db_execution = await _create_execution_record(
            db,
            workflow_id,
            project_id,
            validated_inputs,
            status="running",
            workflow_definition=selected_definition,
            workflow_version=selected_version,
            execution_mode=request.execution_mode,
            release_id=release_id,
            employee_agent_id=employee_agent_id,
            origin=execution_origin,
            workflow_lineage=workflow_lineage,
            usage_owner=usage_owner,
        )
        saved_inputs, saved_definition = await _accepted_execution_payload(
            db,
            db_execution,
            project_id,
        )
        return StreamingResponse(
            _stream_with_owned_session(
                workflow_id,
                project_id,
                db_execution.id,
                saved_inputs,
                saved_definition,
                db_execution.started_at,
                employee_binding=employee_binding,
                workflow_lineage=workflow_lineage,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    elif request.async_mode:
        # --- 2. 异步执行模式 (Celery) ---
        invocation_input_hmac = None
        if approval_invocation_id is not None:
            key = settings.SECRET_KEY
            if key is None or not key.get_secret_value():
                raise HTTPException(503, "员工工作流内部核验未配置")
            invocation_input_hmac = input_receipt_hmac(
                validated_inputs,
                key.get_secret_value(),
                approval_invocation_id,
            )
            existing = await _load_approval_invocation(
                db, approval_invocation_id, project_id
            )
            if existing is not None:
                if not _same_approval_invocation(
                    existing,
                    workflow_id=workflow_id,
                    employee_agent_id=employee_agent_id,
                    inputs=validated_inputs,
                    release_id=release_id,
                    workflow_version=selected_version,
                    definition=selected_definition,
                    origin=execution_origin,
                    workflow_lineage=workflow_lineage,
                    usage_owner=usage_owner,
                ):
                    raise HTTPException(
                        409, "防重复调用标识已用于不同的工作流请求"
                    )
                return existing
        try:
            db_execution = await _create_execution_record(
                db,
                workflow_id,
                project_id,
                validated_inputs,
                status="pending",
                workflow_definition=selected_definition,
                workflow_version=selected_version,
                execution_mode=request.execution_mode,
                release_id=release_id,
                employee_agent_id=employee_agent_id,
                origin=execution_origin,
                workflow_lineage=workflow_lineage,
                execution_id=approval_invocation_id,
                invocation_input_hmac=invocation_input_hmac,
                usage_owner=usage_owner,
            )
        except IntegrityError:
            if approval_invocation_id is None:
                raise
            await db.rollback()
            existing = await _load_approval_invocation(
                db, approval_invocation_id, project_id
            )
            if existing is None or not _same_approval_invocation(
                existing,
                workflow_id=workflow_id,
                employee_agent_id=employee_agent_id,
                inputs=validated_inputs,
                release_id=release_id,
                workflow_version=selected_version,
                definition=selected_definition,
                origin=execution_origin,
                workflow_lineage=workflow_lineage,
                usage_owner=usage_owner,
            ):
                raise HTTPException(
                    409, "防重复调用标识已用于不同的工作流请求"
                ) from None
            return existing

        try:
            await enqueue_workflow(db, db_execution, validated_inputs)
        except WorkflowDispatchError as error:
            raise HTTPException(status_code=503, detail=str(error)) from None

        # Re-query with eager loading to avoid lazy loading issues
        stmt = (
            select(WorkflowExecution)
            .options(selectinload(WorkflowExecution.node_executions))
            .where(WorkflowExecution.id == db_execution.id)
            .execution_options(populate_existing=True)
        )
        result = await db.execute(stmt)
        return result.scalar_one()
    else:
        # --- 3. 同步执行模式 (默认) ---
        db_execution = await _create_execution_record(
            db,
            workflow_id,
            project_id,
            (
                business_query_record_inputs(validated_inputs)
                if has_business_query
                else validated_inputs
            ),
            status="running",
            workflow_definition=selected_definition,
            workflow_version=selected_version,
            execution_mode=request.execution_mode,
            release_id=release_id,
            employee_agent_id=employee_agent_id,
            origin=execution_origin,
            workflow_lineage=workflow_lineage,
            usage_owner=usage_owner,
        )
        saved_inputs, saved_definition = await _accepted_execution_payload(
            db,
            db_execution,
            project_id,
        )
        return await _run_sync_execution(
            workflow_id,
            project_id,
            db_execution.id,
            validated_inputs if has_business_query else saved_inputs,
            saved_definition,
            db_execution.started_at,
            db,
            employee_binding=employee_binding,
            workflow_lineage=workflow_lineage,
            employee_agent_id=employee_agent_id,
            business_context=business_context,
        )


@router.get(
    "/executions/{execution_id}", response_model=WorkflowExecutionSchema
)
async def get_execution_status(
    execution_id: str,
    project_id: str = Query(..., description="Project ID"),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(WorkflowExecution)
        .options(selectinload(WorkflowExecution.node_executions))
        .where(
            WorkflowExecution.id == execution_id,
            WorkflowExecution.project_id == project_id,
        )
    )
    result = await db.execute(query)
    execution = result.scalar_one_or_none()

    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")

    return execution


def _history_anchor(before_started_at: datetime | None, before_id: str | None):
    """Keep pages stable when newer runs arrive between requests."""
    if (before_started_at is None) != (before_id is None):
        raise HTTPException(
            400, "before_started_at and before_id must be provided together"
        )
    if before_started_at is None:
        return None
    # SQLite-backed previews serialize UTC rows without a timezone suffix.
    if before_started_at.tzinfo is None:
        before_started_at = before_started_at.replace(tzinfo=timezone.utc)
    return or_(
        WorkflowExecution.started_at < before_started_at,
        and_(
            WorkflowExecution.started_at == before_started_at,
            WorkflowExecution.id < before_id,
        ),
    )


@router.get(
    "/{workflow_id}/executions", response_model=List[WorkflowExecutionSchema]
)
async def get_workflow_executions(
    workflow_id: str,
    project_id: str = Query(..., description="Project ID"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    before_started_at: datetime | None = Query(None),
    before_id: str | None = Query(None, min_length=1, max_length=64),
    db: AsyncSession = Depends(get_db),
):
    anchor = _history_anchor(before_started_at, before_id)
    query = (
        select(WorkflowExecution)
        .options(selectinload(WorkflowExecution.node_executions))
        .where(
            WorkflowExecution.workflow_id == workflow_id,
            WorkflowExecution.project_id == project_id,
        )
        .order_by(
            desc(WorkflowExecution.started_at), desc(WorkflowExecution.id)
        )
        .offset(skip)
        .limit(limit)
    )
    if anchor is not None:
        query = query.where(anchor)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.get(
    "/executions/by-business-request/{business_request_id}",
    response_model=List[WorkflowExecutionSchema],
)
async def get_business_request_executions(
    business_request_id: uuid.UUID,
    project_id: str = Query(..., description="Project ID"),
    limit: int = Query(20, ge=1, le=100),
    skip: int = Query(0, ge=0),
    visitor_session_id: uuid.UUID | None = Query(None),
    before_started_at: datetime | None = Query(None),
    before_id: str | None = Query(None, min_length=1, max_length=64),
    x_internal_api_key: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
):
    _require_internal_read_access(x_internal_api_key)
    anchor = _history_anchor(before_started_at, before_id)
    conditions = [
        WorkflowExecution.project_id == project_id,
        WorkflowExecution.business_request_id == str(business_request_id),
    ]
    if visitor_session_id is not None:
        conditions.append(
            WorkflowExecution.visitor_session_id == str(visitor_session_id)
        )
    if anchor is not None:
        conditions.append(anchor)
    query = (
        select(WorkflowExecution)
        .options(selectinload(WorkflowExecution.node_executions))
        .where(*conditions)
        .order_by(
            desc(WorkflowExecution.started_at), desc(WorkflowExecution.id)
        )
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(query)
    return list(result.scalars().all())


def _require_internal_read_access(x_internal_api_key: str | None) -> None:
    key = settings.SECRET_KEY
    if key is None or not key.get_secret_value():
        raise HTTPException(503, "工作流内部查询核验未配置")
    if not x_internal_api_key or not hmac.compare_digest(
        x_internal_api_key, key.get_secret_value()
    ):
        raise HTTPException(403, "工作流内部查询身份无法确认")


@router.get(
    "/executions/by-parent/{parent_execution_id}",
    response_model=List[WorkflowExecutionSchema],
)
async def get_child_executions(
    parent_execution_id: uuid.UUID,
    project_id: str = Query(..., description="Project ID"),
    limit: int = Query(20, ge=1, le=100),
    skip: int = Query(0, ge=0),
    before_started_at: datetime | None = Query(None),
    before_id: str | None = Query(None, min_length=1, max_length=64),
    x_internal_api_key: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
):
    _require_internal_read_access(x_internal_api_key)
    anchor = _history_anchor(before_started_at, before_id)
    parent_exists = await db.scalar(
        select(WorkflowExecution.id).where(
            WorkflowExecution.id == str(parent_execution_id),
            WorkflowExecution.project_id == project_id,
        )
    )
    if parent_exists is None:
        raise HTTPException(404, "Parent execution not found")
    query = (
        select(WorkflowExecution)
        .options(selectinload(WorkflowExecution.node_executions))
        .where(
            WorkflowExecution.project_id == project_id,
            WorkflowExecution.parent_execution_id == str(parent_execution_id),
        )
        .order_by(
            desc(WorkflowExecution.started_at), desc(WorkflowExecution.id)
        )
        .offset(skip)
        .limit(limit)
    )
    if anchor is not None:
        query = query.where(anchor)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.get(
    "/executions/by-visitor-session/{visitor_session_id}",
    response_model=List[WorkflowExecutionSchema],
)
async def get_visitor_session_executions(
    visitor_session_id: uuid.UUID,
    project_id: str = Query(..., description="Project ID"),
    limit: int = Query(20, ge=1, le=100),
    skip: int = Query(0, ge=0),
    before_started_at: datetime | None = Query(None),
    before_id: str | None = Query(None, min_length=1, max_length=64),
    x_internal_api_key: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
):
    _require_internal_read_access(x_internal_api_key)
    anchor = _history_anchor(before_started_at, before_id)
    query = (
        select(WorkflowExecution)
        .options(selectinload(WorkflowExecution.node_executions))
        .where(
            WorkflowExecution.project_id == project_id,
            WorkflowExecution.visitor_session_id == str(visitor_session_id),
            WorkflowExecution.business_request_id.isnot(None),
        )
        .order_by(
            desc(WorkflowExecution.started_at), desc(WorkflowExecution.id)
        )
        .offset(skip)
        .limit(limit)
    )
    if anchor is not None:
        query = query.where(anchor)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.post(
    "/executions/{execution_id}/cancel",
    response_model=WorkflowExecutionCancelResponse,
)
async def cancel_execution(
    execution_id: str,
    project_id: str = Query(..., description="Project ID"),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(WorkflowExecution)
        .where(
            WorkflowExecution.id == execution_id,
            WorkflowExecution.project_id == project_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    result = await db.execute(query)
    execution = result.scalar_one_or_none()

    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")

    if execution.status == "cancelled":
        completed_at = execution.completed_at
        if completed_at is None:
            completed_at = datetime.now(timezone.utc)
            execution.completed_at = completed_at
        await scrub_webhook_inputs(db, project_id, execution_id)
        await db.commit()
        return WorkflowExecutionCancelResponse(
            id=execution_id,
            status=ExecutionStatus.CANCELLED,
            cancelled_at=completed_at,
        )

    if execution.status not in [
        "pending",
        "running",
        "waiting_approval",
        "pending_resume",
    ]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel execution in {execution.status} status",
        )

    # Update status in DB
    completed_at = datetime.now(timezone.utc)
    if execution.status == "waiting_approval" and execution.active_approval_id:
        approval = await db.get(
            WorkflowApprovalRequest, execution.active_approval_id
        )
        if (
            approval is not None
            and approval.project_id == project_id
            and approval.execution_id == execution_id
            and approval.status == "pending"
        ):
            approval.status = "cancelled"
            approval.decided_at = completed_at
    execution.status = "cancelled"
    execution.input = None
    execution.completed_at = completed_at
    await scrub_terminal_approval_checkpoints(db, project_id, execution_id)
    await scrub_webhook_inputs(db, project_id, execution_id)
    await db.commit()

    # Do not kill a worker process: it may already be handling another task.
    await revoke_queued_execution(execution_id)

    return WorkflowExecutionCancelResponse(
        id=execution_id,
        status=ExecutionStatus.CANCELLED,
        cancelled_at=completed_at,
    )
