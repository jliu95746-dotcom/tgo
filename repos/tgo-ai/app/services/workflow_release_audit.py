"""Find legacy employee workflow bindings without changing their state."""

import logging
from collections.abc import Awaitable, Callable
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.workflow import AgentWorkflow
from app.schemas.base import PaginationMetadata
from app.schemas.workflow_release_audit import (
    WorkflowReleaseAuditItem,
    WorkflowReleaseAuditResponse,
)
from app.services.workflow_service import WorkflowData, workflow_service_client

logger = logging.getLogger(__name__)

WorkflowReader = Callable[[list[str], str], Awaitable[list[WorkflowData]]]


async def list_unpinned_workflows(
    db: AsyncSession,
    project_id: UUID,
    *,
    limit: int,
    offset: int,
    read_workflows: WorkflowReader | None = None,
) -> WorkflowReleaseAuditResponse:
    (
        'Return project-owned legacy rows and advisory current publication '
        'data.'
    )
    if limit < 1 or limit > 100 or offset < 0:
        raise ValueError("Invalid workflow audit pagination")
    missing_pin = or_(
        AgentWorkflow.release_version.is_(None),
        AgentWorkflow.release_id.is_(None),
        AgentWorkflow.release_digest.is_(None),
    )
    ownership = (
        Agent.project_id == project_id,
        Agent.deleted_at.is_(None),
        AgentWorkflow.deleted_at.is_(None),
        missing_pin,
    )
    total = (
        await db.scalar(
            select(func.count(AgentWorkflow.id))
            .join(Agent, AgentWorkflow.agent_id == Agent.id)
            .where(*ownership)
        )
        or 0
    )
    rows = (
        await db.execute(
            select(Agent, AgentWorkflow)
            .join(AgentWorkflow, AgentWorkflow.agent_id == Agent.id)
            .where(*ownership)
            .order_by(Agent.id, AgentWorkflow.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()

    remote_by_id: dict[str, WorkflowData] = {}
    workflow_ids = sorted({binding.workflow_id for _, binding in rows})
    if workflow_ids:
        try:
            reader = (
                read_workflows or workflow_service_client.get_workflows_batch
            )
            remote_by_id = {
                workflow.id: workflow
                for workflow in await reader(workflow_ids, str(project_id))
            }
        except (
            Exception
        ):  # noqa: BLE001 - local impact report must survive outages
            logger.warning(
                "Workflow owner unavailable during legacy binding audit",
                extra={
                    "project_id": str(project_id),
                    "binding_count": len(rows),
                },
            )

    items = []
    for agent, binding in rows:
        workflow = remote_by_id.get(binding.workflow_id)
        version = workflow.published_version if workflow else None
        if (
            workflow is None
            or workflow.status != "active"
            or type(version) is not int
            or version < 1
        ):
            version = None
        items.append(
            WorkflowReleaseAuditItem(
                binding_id=binding.id,
                agent_id=agent.id,
                agent_name=agent.name,
                agent_active=agent.is_active,
                agent_default=agent.is_default,
                workflow_id=binding.workflow_id,
                workflow_enabled=binding.enabled,
                workflow_name=workflow.name if workflow else None,
                workflow_status=workflow.status if workflow else None,
                current_published_version=version,
                details_available=workflow is not None,
            )
        )
    return WorkflowReleaseAuditResponse(
        data=items,
        pagination=PaginationMetadata(
            total=total,
            limit=limit,
            offset=offset,
            has_next=offset + limit < total,
            has_prev=offset > 0,
        ),
    )
