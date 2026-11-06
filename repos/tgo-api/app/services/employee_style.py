"""Resolve the conversation or employee style within its project."""
from app.schemas.employee_style import EmployeeStyle
from app.services.ai_client import ai_client
from app.services.ai_transport import response_objects


class InactiveEmployeeError(ValueError):
    """No enabled employee can handle this automatic reply."""


async def resolve_employee_style(
    project_id: str,
    agent_id: str | None,
    skill_name: str | None = None,
    enabled: bool = False,
    *,
    require_active: bool = False,
) -> EmployeeStyle:
    # A named conversation setting is explicit, including its off state.
    if skill_name and not require_active:
        return EmployeeStyle(
            skill_name=skill_name,
            enabled=enabled,
            source="conversation",
            agent_id=agent_id,
        )
    if agent_id:
        agent = await ai_client.get_agent(
            project_id, agent_id, include_tools=False
        )
    else:
        result = await ai_client.list_agents(
            project_id, is_default=True, limit=1
        )
        agents = response_objects(result.get("data") or [])
        agent = agents[0] if agents else {}
    if require_active and (not agent.get("id") or not agent.get("is_active")):
        raise InactiveEmployeeError("当前没有可用的 AI 客服，请检查账号启用状态和渠道绑定。")
    raw_name = agent.get("name")
    agent_name = raw_name if isinstance(raw_name, str) else None
    if skill_name:
        return EmployeeStyle(
            skill_name=skill_name,
            enabled=enabled,
            source="conversation",
            agent_id=str(agent["id"]),
            agent_name=agent_name,
        )
    raw_skill = agent.get("humanization_skill_name")
    name = raw_skill if isinstance(raw_skill, str) else None
    return EmployeeStyle(
        agent_id=str(agent["id"]) if agent.get("id") else agent_id,
        agent_name=agent_name,
        skill_name=name,
        enabled=bool(name and agent.get("humanization_skill_enabled", False)),
    )
