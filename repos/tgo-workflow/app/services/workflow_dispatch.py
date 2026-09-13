"""Bounded broker calls; the database remains the execution authority."""

import asyncio

from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.models.execution import WorkflowExecution
from app.services.execution_lifecycle import (
    DISPATCH_FAILURE, fail_pending_dispatch,
)
from celery_app.celery import celery_app
from celery_app.tasks import execute_workflow_task

BROKER_CALL_TIMEOUT = 10


class WorkflowDispatchError(RuntimeError):
    """The run was not claimed and was durably marked as failed."""


async def enqueue_workflow(
    db: AsyncSession, execution: WorkflowExecution,
    inputs: dict[str, JsonValue],
) -> None:
    try:
        await asyncio.wait_for(
            asyncio.to_thread(
                execute_workflow_task.apply_async,
                args=[execution.id, execution.workflow_id, inputs],
                kwargs={"project_id": execution.project_id},
                task_id=execution.id, retry=False,
            ),
            timeout=BROKER_CALL_TIMEOUT,
        )
    except Exception as error:
        logger.warning(
            "Workflow dispatch %s failed: %s",
            execution.id, type(error).__name__,
        )
        # A lost broker acknowledgement does not prove non-delivery. If a
        # worker already claimed the run, leave its status and result alone.
        if await fail_pending_dispatch(db, execution.id, execution.project_id):
            raise WorkflowDispatchError(DISPATCH_FAILURE) from None


async def revoke_queued_execution(execution_id: str) -> None:
    try:
        await asyncio.wait_for(
            asyncio.to_thread(
                celery_app.control.revoke, execution_id, terminate=False,
            ),
            timeout=BROKER_CALL_TIMEOUT,
        )
    except Exception as error:
        # Cancellation is already committed. A delayed delivery still fails
        # the worker's pending-only claim; running nodes check between steps.
        logger.warning(
            "Workflow revoke %s unavailable: %s",
            execution_id, type(error).__name__,
        )
