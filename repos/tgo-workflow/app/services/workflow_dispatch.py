"""Bounded broker calls; the database remains the execution authority."""

import asyncio

from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.models.execution import WorkflowExecution
from app.models.approval import WorkflowApprovalRequest
from app.services.execution_lifecycle import (
    DISPATCH_FAILURE,
    fail_pending_dispatch,
)
from celery_app.celery import celery_app

BROKER_CALL_TIMEOUT = 10


class WorkflowDispatchError(RuntimeError):
    """The run was not claimed and was durably marked as failed."""


class WorkflowResumeDispatchError(RuntimeError):
    """Broker acknowledgement is unknown; approved state remains retryable."""


class WorkflowInitialDispatchError(RuntimeError):
    """Broker acknowledgement is unknown; pending state remains retryable."""


async def enqueue_workflow(
    db: AsyncSession,
    execution: WorkflowExecution,
    inputs: dict[str, JsonValue],
) -> None:
    from celery_app.tasks import execute_workflow_task

    try:
        await asyncio.wait_for(
            asyncio.to_thread(
                execute_workflow_task.apply_async,
                args=(execution.id, execution.workflow_id, inputs),
                kwargs={"project_id": execution.project_id},
                task_id=execution.id,
                retry=False,
            ),
            timeout=BROKER_CALL_TIMEOUT,
        )
    except Exception as error:
        logger.warning(
            "Workflow dispatch %s failed: %s",
            execution.id,
            type(error).__name__,
        )
        # A lost broker acknowledgement does not prove non-delivery. If a
        # worker already claimed the run, leave its status and result alone.
        if await fail_pending_dispatch(db, execution.id, execution.project_id):
            raise WorkflowDispatchError(DISPATCH_FAILURE) from None


async def enqueue_pending_initial(execution: WorkflowExecution) -> None:
    """
    Redeliver a committed pending run; DB claim makes duplicates harmless.
    """
    from celery_app.tasks import execute_workflow_task

    # Event workers load the fixed input from SQL after claiming the run.
    # Keep customer excerpts out of broker payloads and task retry copies.
    dispatch_inputs = (
        {}
        if execution.source_channel
        in {"internal_event", "scheduled_timer", "external_webhook"}
        else execution.input or {}
    )
    try:
        await asyncio.wait_for(
            asyncio.to_thread(
                execute_workflow_task.apply_async,
                args=(execution.id, execution.workflow_id, dispatch_inputs),
                kwargs={"project_id": execution.project_id},
                task_id=execution.id,
                retry=False,
            ),
            timeout=BROKER_CALL_TIMEOUT,
        )
    except Exception as error:
        logger.warning(
            "Workflow initial recovery dispatch %s uncertain: %s",
            execution.id,
            type(error).__name__,
        )
        raise WorkflowInitialDispatchError(
            "工作流仍在等待任务派发，请稍后查看运行记录。"
        ) from None


async def enqueue_approved_resume(
    execution: WorkflowExecution,
    approval: WorkflowApprovalRequest,
) -> None:
    """
    Deliver an approved continuation; DB claim prevents duplicate execution.
    """
    from celery_app.tasks import execute_workflow_task

    try:
        await asyncio.wait_for(
            asyncio.to_thread(
                execute_workflow_task.apply_async,
                args=(
                    execution.id,
                    execution.workflow_id,
                    execution.input or {},
                ),
                kwargs={"project_id": execution.project_id},
                task_id=f"{execution.id}:approval:{approval.id}",
                retry=False,
            ),
            timeout=BROKER_CALL_TIMEOUT,
        )
    except Exception as error:
        logger.warning(
            "Workflow approval resume dispatch %s uncertain: %s",
            approval.id,
            type(error).__name__,
        )
        raise WorkflowResumeDispatchError(
            "审批已保存，恢复任务派发状态未知，请稍后刷新并重试。"
        ) from None


async def revoke_queued_execution(execution_id: str) -> None:
    try:
        await asyncio.wait_for(
            asyncio.to_thread(
                celery_app.control.revoke,
                execution_id,
                terminate=False,
            ),
            timeout=BROKER_CALL_TIMEOUT,
        )
    except Exception as error:
        # Cancellation is already committed. A delayed delivery still fails
        # the worker's pending-only claim; running nodes check between steps.
        logger.warning(
            "Workflow revoke %s unavailable: %s",
            execution_id,
            type(error).__name__,
        )
