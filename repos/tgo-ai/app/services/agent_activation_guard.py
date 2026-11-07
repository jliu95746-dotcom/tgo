"Shared mutation guard: verify the candidate before changing serving \
peers."

from uuid import UUID

from pydantic import JsonValue, TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AuthorizationError, ConflictError
from app.models.agent import Agent, AgentToolAssociation
from app.schemas.agent import AgentCreate
from app.services.agent_trial_fingerprint import private_digest
from app.schemas.agent_activation_requests import ActivationExpectation
from app.services.agent_device_script_selection_identity import (
    selection_snapshot,
)


class ActivationConfirmationConflict(ConflictError):
    (
        'The current server evidence no longer matches the explicitly '
        'confirmed intent.'
    )


json_object = TypeAdapter(dict[str, JsonValue])
BUSINESS_FIELDS = (
    "instruction",
    "model",
    "llm_provider_id",
    "config",
    "is_remote_store_agent",
    "remote_agent_url",
    "store_agent_id",
    "agent_category",
    "bound_device_id",
    "bound_device_policy_digest",
    "skills_enabled",
    "humanization_skill_name",
    "humanization_skill_enabled",
)


def check_new_employee(request: AgentCreate) -> None:
    if request.is_active or request.is_default:
        raise ConflictError("请先保存员工，完成配置、测试和评审后再启用")


def check_live_binding(agent: Agent, current: bool, requested: bool) -> None:
    if agent.is_active and requested and not current:
        raise ConflictError("请先停用员工，再增加业务能力并重新测试后启用")


async def business_fingerprint(db: AsyncSession, agent: Agent) -> str:
    (
        'Compare actual business changes without treating a nickname as a '
        'new test.'
    )
    payload: dict[str, object] = {
        field: getattr(agent, field) for field in BUSINESS_FIELDS
    }
    payload["llm_provider_id"] = (
        str(agent.llm_provider_id) if agent.llm_provider_id else None
    )
    payload["config"] = agent.config or {}
    selection = await selection_snapshot(db, agent.project_id, agent.id)
    if selection is not None:
        payload["device_script_selection"] = selection.model_dump(mode="json")
    bindings = (
        await db.scalars(
            select(AgentToolAssociation).where(
                AgentToolAssociation.agent_id == agent.id,
                AgentToolAssociation.deleted_at.is_(None),
            )
        )
    ).all()
    payload["tools"] = sorted(
        [
            {
                "tool_id": str(row.tool_id),
                "enabled": row.enabled,
                "permissions": sorted(row.permissions or []),
                "config": row.config or {},
            }
            for row in bindings
        ],
        key=lambda row: str(row["tool_id"]),
    )
    payload["collections"] = sorted(
        [
            {"collection_id": row.collection_id, "enabled": row.enabled}
            for row in agent.collections
            if row.deleted_at is None
        ],
        key=lambda row: str(row["collection_id"]),
    )
    payload["workflows"] = sorted(
        [
            {
                "workflow_id": row.workflow_id,
                "enabled": row.enabled,
                "release_version": row.release_version,
                "release_id": row.release_id,
                "release_digest": row.release_digest,
            }
            for row in agent.workflows
            if row.deleted_at is None
        ],
        key=lambda row: str(row["workflow_id"]),
    )
    return private_digest(json_object.validate_python(payload))


async def require_activation(
    db: AsyncSession,
    project: UUID,
    actor: UUID | None,
    agent: UUID,
    *,
    expected: ActivationExpectation | None = None,
) -> None:
    if actor is None:
        raise AuthorizationError("启用或修改接待员工的业务配置需要管理员登录身份")
    # Deferred import keeps the actual AgentService and its read adapter
    # acyclic.
    from app.services.agent_activation_readiness import (
        AgentActivationReadinessService,
    )

    result = await AgentActivationReadinessService(db).read(
        project, actor, agent
    )
    if (
        result.project_id != project
        or result.agent_id != agent
        or result.checked_by != actor
    ):
        raise ConflictError("员工核验范围不一致，请重新检查")
    if not result.can_enable:
        raise ConflictError("员工配置或必测用例尚未通过，请检查缺项并重新测试")
    if expected is not None:
        actual_channels = {
            (
                row.channel,
                row.template_id,
                row.template_version,
                row.policy_revision,
                row.requirements_revision,
            )
            for row in result.channels
        }
        confirmed_channels = {
            (
                row.channel,
                row.template_id,
                row.template_version,
                row.policy_revision,
                row.requirements_revision,
            )
            for row in expected.channels
        }
        if (
            result.configuration_fingerprint
            != expected.configuration_fingerprint
            or result.model_owner_revision != expected.model_owner_revision
            or result.business_evidence_revision
            != expected.business_evidence_revision
            or actual_channels != confirmed_channels
        ):
            raise ActivationConfirmationConflict("确认后的员工配置、模型或岗位渠道已变化，请重新核验")
