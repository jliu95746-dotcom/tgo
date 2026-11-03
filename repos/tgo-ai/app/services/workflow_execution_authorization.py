"""Current AI-side authority for a workflow call queued by an employee."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.agent import Agent
from app.models.workflow import AgentWorkflow


async def require_current_binding(
    db: AsyncSession,
    agent_id: str,
    project_id: str,
    workflow_id: str,
    release_version: int,
    release_id: str,
    release_digest: str,
) -> None:
    """Reject a stale employee run before it starts a workflow execution."""
    try:
        agent_uuid = UUID(agent_id)
        project_uuid = UUID(project_id)
    except ValueError as error:
        raise PermissionError("员工工作流授权已变更，停止执行。") from error

    statement = (
        select(AgentWorkflow.id)
        .join(Agent, AgentWorkflow.agent_id == Agent.id)
        .where(
            Agent.id == agent_uuid,
            Agent.project_id == project_uuid,
            Agent.deleted_at.is_(None),
            Agent.is_active.is_(True),
            AgentWorkflow.workflow_id == workflow_id,
            AgentWorkflow.enabled.is_(True),
            AgentWorkflow.deleted_at.is_(None),
            AgentWorkflow.release_version == release_version,
            AgentWorkflow.release_id == release_id,
            AgentWorkflow.release_digest == release_digest,
        )
    )
    if await db.scalar(statement) is None:
        raise PermissionError("员工工作流授权已变更，停止执行。")


async def verify_current_binding(
    agent_id: str,
    project_id: str,
    workflow_id: str,
    release_version: int,
    release_id: str,
    release_digest: str,
) -> None:
    """Use a fresh database read for every queued tool invocation."""
    async with AsyncSessionLocal() as db:
        await require_current_binding(
            db, agent_id, project_id, workflow_id,
            release_version, release_id, release_digest,
        )
