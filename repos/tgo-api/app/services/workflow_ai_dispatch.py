"""Private Workflow -> API -> AI dispatch with one durable quota claim."""

import asyncio

from anyio import CancelScope
from fastapi import HTTPException
from app.core.exceptions import TGOAPIException
from app.core.logging import get_logger

from app.schemas.workflow_ai_usage import (
    WorkflowAICommand,
    WorkflowAIResult,
    WorkflowAIContext,
    WorkflowAICacheCommand,
    WorkflowAICacheWrite,
)
from app.services import workflow_ai_usage as usage
from app.services.ai_client import ai_client
from app.services.ai_usage_runtime import bind_permit
from app.services.agent_execution_publication import (
    ensure_execution_can_publish,
)
from app.services.workflow_client import workflow_client

logger = get_logger(__name__)


def _cache_command(
    command: WorkflowAICommand, claimed: usage.WorkflowAIClaim
) -> WorkflowAICacheCommand:
    permit = claimed.permit
    return WorkflowAICacheCommand(
        command=command,
        reservation_id=permit.reservation_id if permit else None,
        lease_id=permit.lease_id if permit else None,
    )


async def _confirm(
    command: WorkflowAICommand,
    context: WorkflowAIContext,
    claimed: usage.WorkflowAIClaim,
    answer: WorkflowAIResult,
) -> WorkflowAIResult:
    try:
        await ensure_execution_can_publish(
            ai_client,
            answer.execution_reference,
            project_id=str(command.project_id),
            agent_id=str(command.payload.agent_id),
            channel="internal",
        )
    except HTTPException as error:
        raise HTTPException(
            error.status_code,
            "原回答对应的员工或知识权限无法核验，停止恢复。",
        ) from None
    current = await workflow_client.workflow_ai_context(command)
    if current != context:
        raise HTTPException(403, "当前工作流归属或执行范围已变更。")
    checked = await asyncio.to_thread(usage.recovery_claim, command, current)
    if checked.permit != claimed.permit or (
        checked.replay_execution_id is not None
        and checked.replay_execution_id != answer.execution_id
    ):
        raise HTTPException(403, "原回答与当前额度租约不一致。")
    with CancelScope(shield=True):
        await asyncio.to_thread(usage.complete, checked, answer)
    return answer


async def recover(command: WorkflowAICommand) -> WorkflowAIResult:
    """Read only: no reservation, lease refresh, or model call is allowed."""
    context = await workflow_client.workflow_ai_context(command)
    claimed = await asyncio.to_thread(usage.recovery_claim, command, context)
    cached = await workflow_client.workflow_ai_result(
        "read", _cache_command(command, claimed)
    )
    if cached.state != "completed" or cached.result is None:
        raise HTTPException(409, "原 AI 结果仍待核验，不会重新生成。")
    return await _confirm(command, context, claimed, cached.result)


async def run(command: WorkflowAICommand) -> WorkflowAIResult:
    context = await workflow_client.workflow_ai_context(command)
    claimed = await asyncio.to_thread(usage.claim, command, context)
    if claimed.replay_execution_id is not None:
        return await recover(command)
    dispatched = False
    answer: WorkflowAIResult | None = None
    try:
        cache_command = _cache_command(command, claimed)
        cached = await workflow_client.workflow_ai_result(
            "prepare", cache_command
        )
        if cached.state == "pending":
            raise HTTPException(409, "原 AI 调用仍在处理，不会再次提交。")
        if cached.state == "completed" and cached.result is not None:
            answer = cached.result
            return await _confirm(command, context, claimed, answer)
        current = await workflow_client.workflow_ai_context(command)
        if current != context:
            raise HTTPException(403, "工作流运行范围已变更。")
        with bind_permit(claimed.permit):
            dispatched = True
            answer = await ai_client.run_workflow_agent(
                command.payload,
                command.project_id,
                command.execution_id,
                context.workflow_lineage,
            )
        with CancelScope(shield=True):
            stored = await workflow_client.workflow_ai_result(
                "store",
                WorkflowAICacheWrite(
                    **cache_command.model_dump(),
                    claim_id=cached.claim_id,
                    result=answer,
                ),
            )
            if (
                stored.state != "completed"
                or stored.claim_id != cached.claim_id
                or stored.result != answer
            ):
                raise HTTPException(502, "原回答的保存回执不一致。")
        return await _confirm(command, context, claimed, answer)
    except (Exception, asyncio.CancelledError) as error:
        if answer is not None:
            # The model result is known even when its storage ACK is lost.
            with CancelScope(shield=True):
                try:
                    await asyncio.to_thread(usage.complete, claimed, answer)
                except Exception as receipt_error:
                    logger.warning(
                        "Workflow AI result receipt pending: %s",
                        type(receipt_error).__name__,
                    )
            if isinstance(error, asyncio.CancelledError):
                raise
            if isinstance(
                error, (HTTPException, TGOAPIException)
            ) and error.status_code in {402, 403}:
                raise HTTPException(
                    error.status_code, "当前企业或执行授权已变更，未返回原回答。"
                ) from None
            raise HTTPException(503, "原回答已生成，正在核验保存回执，请勿重新生成。") from None
        known_rejection = not dispatched or (
            isinstance(error, HTTPException)
            and error.status_code in {400, 401, 402, 403, 404, 409, 422, 429}
        )
        with CancelScope(shield=True):
            await asyncio.to_thread(
                usage.reject if known_rejection else usage.unknown,
                claimed,
            )
        if isinstance(error, asyncio.CancelledError):
            raise
        if known_rejection:
            status = (
                error.status_code if isinstance(error, HTTPException) else 403
            )
            raise HTTPException(
                status, "AI 员工、企业额度或当前运行授权无法确认，请检查配置。"
            ) from None
        raise HTTPException(503, "AI 调用结果暂无法确认，请查询原运行记录，避免重复执行。") from None
