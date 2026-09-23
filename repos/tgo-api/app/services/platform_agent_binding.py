"""Validate channel employee bindings through the owning AI service."""

from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ValidationError

from app.services.ai_client import ai_client


class AgentBindingIdentity(BaseModel):
    """Only identity fields are required from the internal agent response."""

    id: UUID
    # Current AgentWithDetails omits project_id; ownership is enforced by
    # its required project-scoped lookup. Check an echo if present.
    project_id: UUID | None = None


async def require_platform_agent(
    project_id: UUID,
    agent_id: UUID | None,
) -> None:
    """Fail before writes when ownership cannot be confirmed.

    An empty binding selects the project's default employee at runtime and
    does not require a lookup. Never query another service's database.
    """
    if agent_id is None:
        return
    try:
        response = await ai_client.get_agent(
            project_id=str(project_id),
            agent_id=str(agent_id),
            include_tools=False,
            include_collections=False,
            include_workflows=False,
        )
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(404, "AI 员工不存在或不属于当前企业") from exc
        raise
    try:
        identity = AgentBindingIdentity.model_validate(response)
    except ValidationError as exc:
        raise HTTPException(502, "无法验证 AI 员工归属，请稍后重试") from exc
    if identity.id != agent_id or (
        identity.project_id is not None and identity.project_id != project_id
    ):
        raise HTTPException(404, "AI 员工不存在或不属于当前企业")
