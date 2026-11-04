"""Read current administrator and template requirements without enabling."""

from fastapi import HTTPException
from uuid import UUID
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError
from app.models.project import Project
from app.models.staff import Staff, StaffRole
from app.schemas.agent_activation import (
    AgentActivationRequirements,
    AgentActivationRequirementsQuery,
)
from app.services.knowledge_review_cases import list_cases
from app.services.scenario_templates import get_template, list_templates


def require_activation_actor(
    db: Session, project_id: UUID, staff_id: UUID
) -> None:
    actor = db.get(Staff, staff_id, populate_existing=True)
    if (
        actor is None
        or actor.project_id != project_id
        or actor.role != StaffRole.ADMIN.value
        or not actor.account_enabled
        or actor.deleted_at is not None
    ):
        raise HTTPException(403, "当前账号没有启用员工的权限")
    project = db.get(Project, project_id, populate_existing=True)
    if project is None or project.deleted_at is not None:
        raise HTTPException(403, "当前账号没有启用员工的权限")


def current_requirements(
    db: Session,
    query: AgentActivationRequirementsQuery,
) -> AgentActivationRequirements:
    require_activation_actor(db, query.project_id, query.requested_by)

    versions = [
        row.version
        for row in list_templates("agents")
        if row.id == query.template_id and row.available
    ]
    if not versions or query.template_version != max(versions):
        raise ConflictError("岗位模板已更新，请刷新并重新检查配置")
    template = get_template(
        "agents", query.template_id, query.template_version
    )
    from app.services.agent_knowledge_cases import current_cases

    catalog = (
        current_cases(db, query.project_id, query.agent_id)
        if query.agent_id
        else list_cases()
    )
    cases = [row for row in catalog if row.template_id == query.template_id]
    if (
        query.agent_id is None
        and query.template_id in {"knowledge", "aftersales"}
        and (
            [row.question for row in cases] != template.test_questions
            or any(row.template_version != template.version for row in cases)
        )
    ):
        raise ConflictError("岗位测试要求已变更，请刷新模板")

    definition = AgentActivationRequirements.model_construct(
        **query.model_dump(),
        required_resources=template.required_resources,
        required_capabilities=template.required_capabilities,
        knowledge_cases=cases,
    )
    definition.requirements_revision = definition.definition_revision()
    try:
        return AgentActivationRequirements.model_validate(
            definition.model_dump()
        )
    except PydanticValidationError:
        raise HTTPException(503, "岗位启用要求暂不可用，请稍后重试") from None
