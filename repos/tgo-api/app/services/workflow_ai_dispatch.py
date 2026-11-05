"""Private Workflow -> API -> AI dispatch with one durable quota claim."""

import asyncio

from anyio import CancelScope
from fastapi import HTTPException

from app.schemas.workflow_ai_usage import WorkflowAICommand, WorkflowAIResult
from app.services import workflow_ai_usage as usage
from app.services.ai_client import ai_client
from app.services.ai_usage_runtime import bind_permit
from app.services.workflow_client import workflow_client


async def run(command: WorkflowAICommand) -> WorkflowAIResult:
    context = await workflow_client.workflow_ai_context(command)
    claimed = await asyncio.to_thread(usage.claim, command, context)
    if claimed.replay_execution_id is not None:
        raise HTTPException(409, "此节点已执行，请查询原运行记录，不要重复生成。")
    dispatched = False
    try:
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
            await asyncio.to_thread(usage.complete, claimed, answer)
        return answer
    except (Exception, asyncio.CancelledError) as error:
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
