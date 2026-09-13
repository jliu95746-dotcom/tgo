import asyncio
from celery_app.celery import celery_app
from celery_app.task_database import workflow_task_session
from app.services.workflow_service import WorkflowService
from app.models.execution import NodeExecution
from app.engine.executor import WorkflowExecutor
from app.integrations.http_client import HttpClient
from app.services.execution_lifecycle import (
    claim_execution, ExecutionStopped, finish_running_execution,
    require_running,
)
from pydantic import JsonValue
from datetime import datetime, timezone
import time
from app.core.logging import logger


@celery_app.task(
    name="execute_workflow_task",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True
)
def execute_workflow_task(
    self, execution_id: str, workflow_id: str,
    inputs: dict[str, JsonValue], project_id: str,
):
    try:
        return asyncio.run(async_execute_workflow(
            execution_id, workflow_id, inputs, project_id,
        ))
    except Exception as exc:
        logger.error("Workflow task failed, retrying: %s", type(exc).__name__)
        raise self.retry(exc=exc)


async def async_execute_workflow(
    execution_id: str, workflow_id: str, inputs: dict[str, JsonValue],
    project_id: str,
) -> None:
    try:
        await _run_workflow(execution_id, workflow_id, inputs, project_id)
    finally:
        # asyncio.run closes this task's loop. Never lend its HTTP connections
        # to the next task, which runs in a different loop.
        try:
            await HttpClient.close_client()
        except Exception as error:
            logger.warning(
                "Workflow HTTP cleanup failed: %s", type(error).__name__,
            )


async def _run_workflow(
    execution_id: str, workflow_id: str, inputs: dict[str, JsonValue],
    project_id: str,
) -> None:
    async with workflow_task_session() as db:
        # 1. Fetch workflow and execution
        workflow = await WorkflowService.get_by_id(db, workflow_id, project_id)
        if not workflow:
            logger.error(
                "Workflow %s not found for project %s",
                workflow_id, project_id,
            )
            return

        if not await claim_execution(
            db, execution_id, workflow_id, project_id,
        ):
            logger.info("Skipping unclaimable workflow run %s", execution_id)
            return

        start_time = time.time()

        async def on_node_start(
            node_id: str, node_type: str, node_data: dict[str, JsonValue],
            index: int,
        ) -> None:
            await require_running(db, execution_id, project_id)

        async def on_node_complete(
            node_id: str, node_type: str, status: str,
            input: dict[str, JsonValue], output: JsonValue,
            error: str | None, duration: int,
        ) -> None:
            await require_running(db, execution_id, project_id)
            logger.info(
                "Node complete: %s (%s) status=%s duration=%sms",
                node_id, node_type, status, duration,
            )
            node_exec = NodeExecution(
                execution_id=execution_id,
                project_id=project_id,
                node_id=node_id,
                node_type=node_type,
                status=status,
                input=input,
                output=output,
                error=error,
                duration=duration,
                started_at=datetime.now(timezone.utc)  # Simplified
            )
            db.add(node_exec)
            await db.commit()

        try:
            executor = WorkflowExecutor(
                workflow.definition, project_id=project_id,
            )
            logger.info(
                "Starting workflow execution: %s for workflow: %s",
                execution_id, workflow_id,
            )
            final_output = await executor.run(
                inputs, on_node_start=on_node_start,
                on_node_complete=on_node_complete,
            )

            duration = int((time.time() - start_time) * 1000)
            changed = await finish_running_execution(
                db, execution_id, project_id, "completed",
                {"result": final_output}, None, duration,
            )
            if changed:
                logger.info(
                    "Workflow execution completed: %s in %sms",
                    execution_id, duration,
                )
        except ExecutionStopped:
            logger.info("Workflow execution stopped: %s", execution_id)
        except Exception as e:
            duration = int((time.time() - start_time) * 1000)
            error = (
                str(e) if isinstance(e, (RuntimeError, ValueError))
                else f"工作流执行失败（{type(e).__name__}），请重试。"
            )
            logger.error(
                "Workflow execution failed: %s %s", execution_id, error,
            )
            await db.rollback()
            await finish_running_execution(
                db, execution_id, project_id, "failed", None, error, duration,
            )

        await db.commit()
