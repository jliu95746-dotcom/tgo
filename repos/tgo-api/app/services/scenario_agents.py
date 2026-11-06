"""Materialize login-owned setup configuration through the existing AI client."""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError
from app.models.ai_provider import AIProvider
from app.schemas.scenario_agents import (
    ScenarioAgentReceipt,
    ScenarioAgentSnapshot,
    SetupAgentSnapshot,
    SetupToolBinding,
)
from app.schemas.scenario_drafts import ScenarioDraftResponse
from app.services.ai_client import ai_client
from app.services.scenario_drafts import get_draft
from app.schemas.agent_activation import AgentActivationSelection


def _snapshot(
    db: Session, draft: ScenarioDraftResponse
) -> ScenarioAgentSnapshot:
    if draft.module != "agents":
        raise ConflictError("此草稿不是员工配置")
    business, bindings, settings = (
        draft.business,
        draft.bindings,
        draft.agent_settings,
    )
    if not all(
        value.strip()
        for value in (
            business.name,
            business.profession,
            business.company_summary,
            business.instructions,
        )
    ):
        raise ConflictError("请先补齐员工职责和企业信息")
    if len(bindings.device_ids) > 1 or bindings.connection_ids:
        raise ConflictError("当前员工配置仅支持一个设备；业务连接尚未开放")
    model = bindings.model
    if model is not None:
        provider = db.scalar(
            select(AIProvider.id).where(
                AIProvider.id == model.provider_id,
                AIProvider.project_id == draft.project_id,
                AIProvider.deleted_at.is_(None),
                AIProvider.is_active.is_(True),
            )
        )
        if provider is None:
            raise NotFoundError("模型连接")
    return ScenarioAgentSnapshot(
        revision=draft.revision,
        activation_selection=AgentActivationSelection.model_validate(
            {
                "template_id": draft.template_id,
                "template_version": draft.template_version,
                "channel": draft.channel,
            }
        ),
        agent=SetupAgentSnapshot(
            name=business.name.strip(),
            instruction=business.instructions,
            model=model.model_id if model else "__system_default__",
            llm_provider_id=model.provider_id if model else None,
            config={
                "profession": business.profession,
                "company_summary": business.company_summary,
                "markdown": settings.markdown,
                "add_datetime_to_context": settings.add_datetime_to_context,
                "show_tool_calls": settings.show_tool_calls,
                "tool_call_limit": settings.tool_call_limit,
                "num_history_runs": settings.num_history_runs,
            },
            tools=[
                SetupToolBinding(tool_id=value) for value in bindings.tool_ids
            ],
            collections=[str(value) for value in bindings.knowledge_ids],
            workflows=[str(value) for value in bindings.workflow_ids],
            bound_device_id=str(bindings.device_ids[0])
            if bindings.device_ids
            else None,
            skills_enabled=settings.skills_enabled,
            humanization_skill_name=settings.humanization_skill_name,
            humanization_skill_enabled=settings.humanization_skill_enabled,
        ),
    )


async def save(
    db: Session,
    project: UUID,
    staff: UUID,
    draft_id: UUID,
    expected_revision: int,
) -> ScenarioAgentReceipt:
    draft = get_draft(db, project, staff, draft_id)
    if draft.revision != expected_revision:
        raise ConflictError("草稿已有新的配置，请重新读取后再保存员工")
    receipt = await ai_client.save_agent_draft(
        project, staff, draft_id, _snapshot(db, draft)
    )
    # A concurrently saved setup must not be presented as already materialized.
    if get_draft(db, project, staff, draft_id).revision != expected_revision:
        raise ConflictError("草稿已在其他页面修改，请重新保存员工配置")
    return receipt


async def get(
    db: Session, project: UUID, staff: UUID, draft_id: UUID
) -> ScenarioAgentReceipt:
    draft = get_draft(db, project, staff, draft_id)
    if draft.module != "agents":
        raise NotFoundError("员工配置草稿")
    return await ai_client.get_agent_draft(project, staff, draft_id)
