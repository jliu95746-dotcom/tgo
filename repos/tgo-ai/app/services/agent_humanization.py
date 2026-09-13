"""Validate an employee's optional expression-only skill."""
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.schemas.skill import SkillEmployeeUsage
from app.config import settings
from app.exceptions import ValidationError
from app.services.skill_file_service import SkillFileService, SkillNotFoundError


async def validate_humanization_binding(project_id: UUID, name: str | None, enabled: bool) -> None:
    if not name:
        if enabled:
            raise ValidationError("请先选择拟人技能，再打开开关")
        return
    try:
        skill = await SkillFileService(settings.skills_base_dir).get_skill(str(project_id), name)
    except (SkillNotFoundError, ValueError) as exc:
        raise ValidationError("拟人技能不存在，请重新选择") from exc
    if skill.skill_type != "humanization":
        raise ValidationError("这里只能绑定拟人技能，普通技能请使用专业技能开关")


async def humanization_usage(db: AsyncSession, project_id: str) -> dict[str, list[SkillEmployeeUsage]]:
    rows = await db.execute(select(
        Agent.id, Agent.name, Agent.humanization_skill_name, Agent.humanization_skill_enabled,
    ).where(Agent.project_id == UUID(project_id), Agent.deleted_at.is_(None),
            Agent.humanization_skill_name.is_not(None)))
    result: dict[str, list[SkillEmployeeUsage]] = {}
    for agent_id, name, skill_name, enabled in rows:
        result.setdefault(skill_name, []).append(SkillEmployeeUsage(id=str(agent_id), name=name, enabled=enabled))
    return result
