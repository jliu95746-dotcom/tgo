"""Bound nested workflow invocations across Workflow -> AI -> Workflow hops."""

from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.models.execution import WorkflowExecution
from app.schemas.execution import WorkflowExecutionOrigin

MAX_WORKFLOW_DEPTH = 4


def extend_workflow_lineage(
    lineage: tuple[str, ...], workflow_id: str
) -> tuple[str, ...]:
    if len(set(lineage)) != len(lineage):
        raise ValueError("工作流调用链包含循环")
    if workflow_id in lineage:
        raise ValueError("工作流调用链包含循环")
    if len(lineage) >= MAX_WORKFLOW_DEPTH:
        raise ValueError("工作流调用层级超过上限")
    return (*lineage, workflow_id)


async def require_parent_execution(
    db: AsyncSession,
    origin: WorkflowExecutionOrigin | None,
    project_id: str,
) -> WorkflowExecutionOrigin | None:
    if origin is None or origin.parent_execution_id is None:
        return origin
    parent = await db.get(WorkflowExecution, str(origin.parent_execution_id))
    if (
        parent is None
        or parent.project_id != project_id
        or parent.status != "running"
        or tuple(parent.workflow_lineage or ()) != origin.workflow_lineage
        or parent.workflow_id != origin.workflow_lineage[-1]
    ):
        raise PermissionError("父工作流运行记录与当前企业或调用链不一致")
    if (parent.business_request_id is None) != (
        parent.visitor_session_id is None
    ):
        raise PermissionError("父工作流客户来源不完整")
    if (parent.source_message_id is None) != (parent.source_channel is None):
        raise PermissionError("父工作流原消息来源不完整")
    if parent.business_request_id is None:
        if parent.source_message_id is not None:
            raise PermissionError("父工作流原消息缺少客户业务来源")
        if (
            origin.business_request_id is not None
            or origin.source_message_id is not None
        ):
            raise PermissionError("父工作流没有客户业务来源")
        return origin
    try:
        parent_project = UUID(parent.project_id)
        parent_request = UUID(parent.business_request_id)
        parent_session = UUID(parent.visitor_session_id)
    except ValueError:
        raise PermissionError("父工作流客户来源无效") from None
    if origin.business_request_id is not None and (
        origin.business_project_id != parent_project
        or origin.business_request_id != parent_request
        or origin.visitor_session_id != parent_session
    ):
        raise PermissionError("子工作流客户来源与父流程不一致")
    if origin.source_message_id is not None and (
        origin.source_message_id != parent.source_message_id
        or origin.source_channel != parent.source_channel
    ):
        raise PermissionError("子工作流原消息与父流程不一致")
    try:
        return WorkflowExecutionOrigin.model_validate(
            {
                **origin.model_dump(),
                "business_project_id": parent_project,
                "business_request_id": parent_request,
                "visitor_session_id": parent_session,
                "source_message_id": parent.source_message_id,
                "source_channel": parent.source_channel,
            }
        )
    except ValueError:
        raise PermissionError("父工作流原消息来源无效") from None
