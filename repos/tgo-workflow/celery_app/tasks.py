import asyncio
from celery_app.celery import celery_app
from celery_app.task_database import workflow_task_session
from app.services.execution_snapshot import load_execution_snapshot
from app.services.event_trigger import (
    require_active_internal_event, prepare_internal_event_claim,
)
from app.services.event_inputs import frozen_event_inputs
from app.services.timer_runtime import require_active_timer
from app.services.webhook_runtime import require_active_webhook, webhook_inputs
from app.services.event_execution_expiry import (
    event_execution_cutoff, expire_internal_event, running_segment_started_at,
    SOURCE_EVENT_PENDING_RETENTION, expire_pending_source_event,
)
from app.services.approval_state import (
    approved_resume_assignee, claim_approved_resume, expire_approval,
    fail_revoked_approved_resume, pause_for_approval, reserve_resume_dispatch,
    RESUME_DISPATCH_INTERVAL,
)
from app.services.workflow_dispatch import (
    WorkflowInitialDispatchError, WorkflowResumeDispatchError,
    enqueue_pending_initial, enqueue_approved_resume,
)
from app.services.approval_preview import render_approval_preview
from app.services.approval_resume_recovery import (
    begin_resume_node, finish_resume_node,
)
from app.services.approval_resume_progress import save_resume_progress
from app.services.aftersales_review import (
    ACTION_NAME, create_approved_review, render_review_approval_preview,
)
from app.integrations.api_client import verify_approval_assignees
from app.models.approval import WorkflowApprovalRequest
from app.models.execution import WorkflowExecution
from sqlalchemy import or_, select
from app.engine.checkpoint import ExecutionCheckpoint, WorkflowPaused
from app.schemas.node import ApprovalNodeData
from app.services.employee_workflow_binding import check_employee_binding
from app.services.validation_service import ValidationService
from app.services.workflow_inputs import validate_inputs
from app.models.execution import NodeExecution
from app.engine.executor import WorkflowExecutor, public_execution_error
from app.integrations.http_client import HttpClient
from app.services.execution_lifecycle import (
    INITIAL_DISPATCH_FAST_ATTEMPTS, INITIAL_DISPATCH_GRACE,
    claim_execution, ExecutionStopped, finish_running_execution,
    initial_dispatch_due, read_execution_status, require_running,
    reserve_initial_dispatch,
)
from app.services.parallel_node_receipts import preserve_cancelled_parallel_node
from pydantic import JsonValue
from datetime import datetime, timedelta, timezone
import time
from app.core.logging import logger


@celery_app.task(
    name="expire_workflow_approvals", bind=True,
    max_retries=3, default_retry_delay=60, acks_late=True,
)
def expire_workflow_approvals_task(self) -> int:
    try:
        return asyncio.run(sweep_expired_approvals())
    except Exception as error:
        logger.error("Workflow approval expiry sweep failed: %s", type(error).__name__)
        raise self.retry(exc=error)


async def sweep_expired_approvals(*, now: datetime | None = None) -> int:
    """Bound each pass; late or duplicate beat deliveries are safe."""
    current = now or datetime.now(timezone.utc)
    async with workflow_task_session() as db:
        rows = (await db.execute(
            select(WorkflowApprovalRequest.id, WorkflowApprovalRequest.project_id)
            .where(
                WorkflowApprovalRequest.status == "pending",
                WorkflowApprovalRequest.expires_at <= current,
            )
            .order_by(WorkflowApprovalRequest.expires_at)
            .limit(100)
        )).all()
        expired = 0
        for approval_id, project_id in rows:
            if await expire_approval(db, project_id, approval_id, now=current):
                expired += 1
        return expired


@celery_app.task(
    name="resume_pending_workflow_approvals", bind=True,
    max_retries=3, default_retry_delay=60, acks_late=True,
)
def resume_pending_workflow_approvals_task(self) -> int:
    try:
        return asyncio.run(sweep_pending_resumes())
    except Exception as error:
        logger.error("Workflow approval resume sweep failed: %s", type(error).__name__)
        raise self.retry(exc=error)


async def sweep_pending_resumes(*, now: datetime | None = None) -> int:
    """Redeliver approved runs after a lost acknowledgement or process restart."""
    current = now or datetime.now(timezone.utc)
    async with workflow_task_session() as db:
        rows = (await db.execute(
            select(WorkflowExecution, WorkflowApprovalRequest)
            .join(
                WorkflowApprovalRequest,
                WorkflowExecution.active_approval_id == WorkflowApprovalRequest.id,
            )
            .where(
                WorkflowExecution.project_id == WorkflowApprovalRequest.project_id,
                WorkflowExecution.id == WorkflowApprovalRequest.execution_id,
                WorkflowExecution.status == "pending_resume",
                WorkflowApprovalRequest.status == "approved",
                or_(
                    WorkflowApprovalRequest.last_resume_dispatch_at.is_(None),
                    WorkflowApprovalRequest.last_resume_dispatch_at
                    <= current - RESUME_DISPATCH_INTERVAL,
                ),
            )
            .order_by(WorkflowApprovalRequest.decided_at, WorkflowApprovalRequest.id)
            .limit(100)
        )).all()
        delivered = 0
        for run, approval in rows:
            if not await reserve_resume_dispatch(db, run.project_id, approval.id, now=current):
                continue
            try:
                await enqueue_approved_resume(run, approval)
            except WorkflowResumeDispatchError:
                continue
            delivered += 1
        return delivered


@celery_app.task(
    name="recover_pending_initial_workflows", bind=True,
    max_retries=3, default_retry_delay=60, acks_late=True,
)
def recover_pending_initial_workflows_task(self) -> int:
    try:
        return asyncio.run(sweep_pending_initial())
    except Exception as error:
        logger.error("Workflow initial dispatch sweep failed: %s", type(error).__name__)
        raise self.retry(exc=error)


async def sweep_pending_initial(*, now: datetime | None = None) -> int:
    """Recover committed employee runs missed by initial broker publication."""
    current = now or datetime.now(timezone.utc)
    async with workflow_task_session() as db:
        rows = (await db.scalars(
            select(WorkflowExecution)
            .where(
                WorkflowExecution.status == "pending",
                WorkflowExecution.execution_mode == "production",
                or_(WorkflowExecution.employee_agent_id.is_not(None),
                    WorkflowExecution.source_channel.in_(("internal_event", "scheduled_timer", "external_webhook"))),
                WorkflowExecution.release_id.is_not(None),
                WorkflowExecution.definition_snapshot.is_not(None),
                WorkflowExecution.definition_digest.is_not(None),
                WorkflowExecution.workflow_version.is_not(None),
                WorkflowExecution.started_at <= current - INITIAL_DISPATCH_GRACE,
                initial_dispatch_due(current),
            )
            .order_by(WorkflowExecution.started_at, WorkflowExecution.id)
            .limit(100)
        )).all()
        delivered = 0
        for run in rows:
            if not await reserve_initial_dispatch(db, run.project_id, run.id,
                                                  now=current):
                continue
            if run.initial_dispatch_attempts >= INITIAL_DISPATCH_FAST_ATTEMPTS:
                logger.warning("Workflow initial dispatch remains pending: %s", run.id)
            try:
                await enqueue_pending_initial(run)
            except WorkflowInitialDispatchError:
                continue
            delivered += 1
        return delivered


@celery_app.task(
    name="expire_running_internal_events", bind=True,
    max_retries=3, default_retry_delay=60, acks_late=True,
)
def expire_running_internal_events_task(self) -> int:
    try:
        return asyncio.run(sweep_expired_internal_events())
    except Exception as error:
        logger.error("Internal event expiry sweep failed: %s", type(error).__name__)
        raise self.retry(exc=error)


async def sweep_expired_internal_events(*, now: datetime | None = None) -> int:
    """Stop bounded event runs after their hard deadline, without replay."""
    current = now or datetime.now(timezone.utc)
    hard_limit = execute_workflow_task.time_limit
    if hard_limit is None:
        hard_limit = celery_app.conf.task_time_limit
    cutoff = event_execution_cutoff(current, hard_limit)
    if cutoff is None:
        return 0
    async with workflow_task_session() as db:
        rows = (await db.execute(
            select(WorkflowExecution.id, WorkflowExecution.project_id)
            .where(
                WorkflowExecution.source_channel.in_(("internal_event", "scheduled_timer", "external_webhook")),
                WorkflowExecution.execution_mode == "production",
                WorkflowExecution.status == "running",
                WorkflowExecution.active_approval_id.is_(None),
                running_segment_started_at() <= cutoff,
            )
            .order_by(running_segment_started_at(), WorkflowExecution.id)
            .limit(100)
        )).all()
        expired = 0
        for execution_id, project_id in rows:
            if await expire_internal_event(db, project_id, execution_id, cutoff, current):
                expired += 1
        return expired


@celery_app.task(name="expire_pending_source_inputs", acks_late=True)
def expire_pending_source_inputs_task() -> int:
    try:
        return asyncio.run(sweep_pending_source_expiry())
    except Exception as error:
        logger.error("Source input expiry sweep failed: %s", type(error).__name__)
        raise


async def sweep_pending_source_expiry(*, now: datetime | None = None) -> int:
    current = now or datetime.now(timezone.utc)
    cutoff = current - SOURCE_EVENT_PENDING_RETENTION
    async with workflow_task_session() as db:
        rows = (await db.execute(
            select(WorkflowExecution.id, WorkflowExecution.project_id)
            .where(
                WorkflowExecution.source_channel == "internal_event",
                WorkflowExecution.execution_mode == "production",
                WorkflowExecution.visitor_session_id.is_not(None),
                WorkflowExecution.status == "pending",
                WorkflowExecution.started_at <= cutoff,
            )
            .order_by(WorkflowExecution.started_at, WorkflowExecution.id)
            .limit(100)
        )).all()
        expired = 0
        for execution_id, project_id in rows:
            if await expire_pending_source_event(
                db, project_id, execution_id, cutoff, current,
            ):
                expired += 1
        return expired


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
        candidate = await db.get(WorkflowExecution, execution_id)
        if (candidate is not None and candidate.project_id == project_id
                and candidate.workflow_id == workflow_id
                and candidate.source_channel == "internal_event"):
            try:
                now = datetime.now(timezone.utc)
                if candidate.status == "pending" and await expire_pending_source_event(
                    db, project_id, execution_id,
                    now - SOURCE_EVENT_PENDING_RETENTION, now,
                ):
                    return
                if not await prepare_internal_event_claim(db, candidate):
                    logger.warning(
                        "Workflow event retained for key recovery: %s",
                        execution_id,
                    )
                    return
            except ExecutionStopped:
                return
        if (candidate is not None and candidate.project_id == project_id
                and candidate.workflow_id == workflow_id
                and candidate.source_channel == "external_webhook"
                and candidate.status in {"pending", "pending_resume"}):
            try:
                await require_active_webhook(
                    db, project_id, workflow_id, execution_id,
                    expected_status="pending" if candidate.status == "pending" else "pending_resume",
                )
            except ExecutionStopped:
                return
        checkpoint: ExecutionCheckpoint | None = None
        approved_resume_assignee_id: str | None = None
        approved_resume_approval_id: str | None = None
        resume_segment: datetime | None = None
        resume_node_receipts: dict[str, str] = {}
        if not await claim_execution(db, execution_id, workflow_id, project_id):
            if await read_execution_status(db, execution_id, project_id) != "pending_resume":
                logger.info("Skipping unclaimable workflow run %s", execution_id)
                return
            assigned = await approved_resume_assignee(
                db, project_id, execution_id, workflow_id,
            )
            if assigned is None:
                logger.info("Skipping ineligible approval resume %s", execution_id)
                return
            approval_id, approver_id = assigned
            try:
                await verify_approval_assignees(project_id, {approver_id})
            except PermissionError:
                await fail_revoked_approved_resume(db, project_id, execution_id, approval_id)
                logger.info("Approval assignee revoked before resume: %s", execution_id)
                return
            resume_segment = datetime.now(timezone.utc)
            checkpoint = await claim_approved_resume(
                db, project_id, execution_id, workflow_id,
                expected_approval_id=approval_id,
                expected_approver_id=approver_id,
                segment_started_at=resume_segment,
            )
            if checkpoint is None:
                logger.info("Skipping already-claimed approval resume %s", execution_id)
                return
            approved_resume_assignee_id = approver_id
            approved_resume_approval_id = approval_id

        start_time = time.time()
        employee_binding = None
        current_run = await db.get(WorkflowExecution, execution_id)
        internal_event = current_run is not None and current_run.source_channel == "internal_event"
        scheduled_timer = current_run is not None and current_run.source_channel == "scheduled_timer"
        external_webhook = current_run is not None and current_run.source_channel == "external_webhook"

        async def check_active() -> None:
            await require_running(
                db, execution_id, project_id,
                expected_segment_started_at=resume_segment,
            )
            if internal_event:
                await require_active_internal_event(db, project_id, workflow_id, execution_id)
            if scheduled_timer:
                await require_active_timer(db, project_id, workflow_id, execution_id)
            if external_webhook:
                await require_active_webhook(db, project_id, workflow_id, execution_id)
            await check_employee_binding(employee_binding, db)
            if approved_resume_assignee_id is not None:
                await verify_approval_assignees(
                    project_id, {approved_resume_assignee_id},
                )
                await require_running(
                    db, execution_id, project_id,
                    expected_segment_started_at=resume_segment,
                )

        async def on_node_start(
            node_id: str, node_type: str, node_data: dict[str, JsonValue],
            index: int,
        ) -> None:
            await check_active()
            if resume_segment is not None:
                resume_node_receipts[node_id] = await begin_resume_node(
                    db, project_id, execution_id, node_id, node_type,
                    resume_segment,
                )

        async def on_node_complete(
            node_id: str, node_type: str, status: str,
            input: dict[str, JsonValue], output: JsonValue,
            error: str | None, duration: int,
            started_at: datetime | None = None,
            completed_at: datetime | None = None,
        ) -> None:
            receipt_id = resume_node_receipts.get(node_id)
            if status == "cancelled" and receipt_id is not None:
                await finish_resume_node(
                    db, project_id, execution_id, receipt_id, resume_segment,
                    status=status, inputs={"capture_status": "redacted"},
                    output=None, error=None, duration=duration,
                    completed_at=completed_at,
                )
                resume_node_receipts.pop(node_id, None)
                return
            if status == "cancelled" and await preserve_cancelled_parallel_node(
                db, project_id=project_id, execution_id=execution_id,
                node_id=node_id, node_type=node_type,
                started_at=started_at, completed_at=completed_at, duration=duration,
            ):
                return
            await require_running(
                db, execution_id, project_id,
                expected_segment_started_at=resume_segment,
            )
            if internal_event:
                await require_active_internal_event(db, project_id, workflow_id, execution_id)
            if scheduled_timer:
                await require_active_timer(db, project_id, workflow_id, execution_id, retain_lock=True)
            if external_webhook:
                await require_active_webhook(db, project_id, workflow_id, execution_id, retain_lock=True)
            await check_employee_binding(employee_binding, db)
            if approved_resume_assignee_id is not None:
                await verify_approval_assignees(
                    project_id, {approved_resume_assignee_id},
                )
            logger.info(
                "Node complete: %s (%s) status=%s duration=%sms",
                node_id, node_type, status, duration,
            )
            if receipt_id is not None:
                await finish_resume_node(
                    db, project_id, execution_id, receipt_id, resume_segment,
                    status=status, inputs=input, output=output,
                    error=error, duration=duration, completed_at=completed_at,
                    commit=status != "completed",
                )
                resume_node_receipts.pop(node_id, None)
                return
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
                started_at=started_at or datetime.now(timezone.utc),
                completed_at=completed_at,
            )
            db.add(node_exec)
            if node_type == "approval" and status == "completed":
                # Commit this receipt with its wait state and checkpoint.
                await db.flush()
            else:
                await db.commit()

        try:
            snapshot = await load_execution_snapshot(db, execution_id, workflow_id, project_id)
            employee_binding = snapshot.employee_binding
            validation_errors = (
                ValidationService.validate_workflow(snapshot.definition, allow_timer_preparation=True)
                if scheduled_timer else ValidationService.validate_workflow(
                    snapshot.definition, allow_webhook_preparation=external_webhook)
            )
            if validation_errors:
                raise RuntimeError("执行快照中的工作流未通过校验，请重新提交测试。")
            if external_webhook:
                fixed_inputs = await webhook_inputs(db, project_id, workflow_id, execution_id)
            elif internal_event and current_run is not None:
                fixed_inputs = frozen_event_inputs(current_run)
            else:
                fixed_inputs = snapshot.inputs
            accepted_inputs = validate_inputs(snapshot.definition, fixed_inputs)
            raw_nodes = snapshot.definition.get("nodes", [])
            if not isinstance(raw_nodes, list):
                raise RuntimeError("执行快照中的节点定义无效。")
            approval_nodes = {
                str(node["id"]): ApprovalNodeData.model_validate(node["data"])
                for node in raw_nodes
                if isinstance(node, dict) and node.get("type") == "approval"
            }

            async def on_checkpoint(
                node_id: str, node_type: str, saved: ExecutionCheckpoint,
            ) -> bool:
                if node_type != "approval":
                    if (resume_segment is not None
                            and approved_resume_approval_id is not None):
                        await save_resume_progress(
                            db, project_id, execution_id,
                            approved_resume_approval_id, resume_segment, saved,
                        )
                    return False
                config = approval_nodes[node_id]
                await verify_approval_assignees(project_id, {config.approver_staff_id})
                if config.after_approval_action == ACTION_NAME:
                    run = await db.get(WorkflowExecution, execution_id)
                    if run is None or not run.visitor_session_id or not config.case_category:
                        raise RuntimeError("售后核对任务缺少类别或可信客户会话。")
                    preview = render_review_approval_preview(
                        config.summary, saved.context_data,
                        config.case_category, run.visitor_session_id,
                    )
                else:
                    preview = render_approval_preview(
                        config.summary, saved.context_data,
                    )
                await pause_for_approval(
                    db, project_id, execution_id, node_id,
                    config.approver_staff_id,
                    preview, saved,
                    expires_at=datetime.now(timezone.utc)
                    + timedelta(minutes=config.expires_in_minutes),
                    expected_segment_started_at=resume_segment,
                )
                return True

            executor = WorkflowExecutor(
                snapshot.definition, project_id=project_id,
                execution_id=execution_id,
                workflow_lineage=getattr(snapshot, "workflow_lineage", (workflow_id,)),
                check_active=check_active,
            )
            logger.info(
                "Starting workflow execution: %s for workflow: %s",
                execution_id, workflow_id,
            )
            if checkpoint is not None and approved_resume_approval_id is not None:
                request = await db.get(WorkflowApprovalRequest, approved_resume_approval_id)
                if request is None:
                    raise RuntimeError("原审批记录已不存在，不能恢复售后核对任务。")
                action_config = approval_nodes.get(request.node_id)
                if action_config is not None and action_config.after_approval_action == ACTION_NAME:
                    await require_running(
                        db, execution_id, project_id,
                        expected_segment_started_at=resume_segment,
                    )
                    await check_employee_binding(employee_binding, db)
                    await verify_approval_assignees(project_id, {request.approver_staff_id})
                    await create_approved_review(
                        db, project_id, execution_id, approved_resume_approval_id,
                        expected_segment_started_at=resume_segment,
                    )
            if approval_nodes or checkpoint is not None:
                final_output = await executor.run(
                    accepted_inputs, on_node_start=on_node_start,
                    on_node_complete=on_node_complete,
                    checkpoint=checkpoint, on_checkpoint=on_checkpoint,
                )
            else:
                final_output = await executor.run(
                    accepted_inputs, on_node_start=on_node_start,
                    on_node_complete=on_node_complete,
                )
            await check_employee_binding(employee_binding, db)
            if internal_event:
                await require_active_internal_event(db, project_id, workflow_id, execution_id)
            if scheduled_timer:
                await require_active_timer(db, project_id, workflow_id, execution_id, retain_lock=True)

            if external_webhook:
                await require_active_webhook(db, project_id, workflow_id, execution_id, retain_lock=True)

            duration = int((time.time() - start_time) * 1000)
            changed = await finish_running_execution(
                db, execution_id, project_id, "completed",
                {"result": final_output}, None, duration,
                expected_segment_started_at=resume_segment,
            )
            if changed:
                logger.info(
                    "Workflow execution completed: %s in %sms",
                    execution_id, duration,
                )
        except ExecutionStopped:
            logger.info("Workflow execution stopped: %s", execution_id)
        except WorkflowPaused:
            logger.info("Workflow execution waiting for approval: %s", execution_id)
        except Exception as e:
            duration = int((time.time() - start_time) * 1000)
            error = public_execution_error(e)
            logger.error(
                "Workflow execution failed: %s %s", execution_id, error,
            )
            await db.rollback()
            await finish_running_execution(
                db, execution_id, project_id, "failed", None, error, duration,
                expected_segment_started_at=resume_segment,
            )

        await db.commit()
