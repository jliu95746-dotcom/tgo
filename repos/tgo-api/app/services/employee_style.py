"""Resolve a conversation override or the actual employee's style, never another project."""
from app.schemas.employee_style import EmployeeStyle
from app.services.ai_client import ai_client


async def resolve_employee_style(
    project_id: str, agent_id: str | None,
    skill_name: str | None = None, enabled: bool = False,
) -> EmployeeStyle:
    # A named conversation setting is explicit, including its off state.
    if skill_name:
        return EmployeeStyle(skill_name=skill_name, enabled=enabled,
                             source="conversation", agent_id=agent_id)
    if agent_id:
        agent = await ai_client.get_agent(project_id, agent_id, include_tools=False)
    else:
        result = await ai_client.list_agents(project_id, is_default=True, limit=1)
        agents = result.get("data", [])
        agent = agents[0] if agents else {}
    name = agent.get("humanization_skill_name")
    return EmployeeStyle(
        agent_id=str(agent["id"]) if agent.get("id") else agent_id,
        agent_name=agent.get("name"), skill_name=name,
        enabled=bool(name and agent.get("humanization_skill_enabled", False)),
    )
