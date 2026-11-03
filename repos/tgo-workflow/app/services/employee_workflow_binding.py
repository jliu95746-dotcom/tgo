"""Current authorization for a saved employee workflow execution."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.ai_client import AIClient
from app.models.workflow import Workflow


@dataclass(frozen=True)
class EmployeeWorkflowBinding:
    project_id: str
    agent_id: str
    workflow_id: str
    release_version: int
    release_id: str
    release_digest: str


def saved_employee_binding(
    project_id: str,
    agent_id: str | None,
    workflow_id: str,
    execution_mode: str | None,
    release_version: int | None,
    release_id: str | None,
    release_digest: str | None,
) -> EmployeeWorkflowBinding | None:
    if agent_id is None:
        return None
    if (
        execution_mode != "production"
        or release_version is None
        or release_version < 1
        or not release_id
        or not release_digest
    ):
        raise RuntimeError("员工工作流执行记录缺少固定发布版本")
    return EmployeeWorkflowBinding(
        project_id,
        agent_id,
        workflow_id,
        release_version,
        release_id,
        release_digest,
    )


async def check_employee_binding(
    binding: EmployeeWorkflowBinding | None, db: AsyncSession
) -> None:
    if binding is None:
        return
    await AIClient.verify_employee_workflow_binding(
        binding.project_id,
        binding.agent_id,
        binding.workflow_id,
        binding.release_version,
        binding.release_id,
        binding.release_digest,
    )
    status = await db.scalar(
        select(Workflow.status).where(
            Workflow.id == binding.workflow_id,
            Workflow.project_id == binding.project_id,
        )
    )
    await db.commit()
    if status != "active":
        raise PermissionError("工作流已停用，停止员工执行")
