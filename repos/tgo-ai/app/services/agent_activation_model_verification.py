"""Attach current model-owner evidence to a configuration, never a credential receipt."""

import hmac
from typing import Protocol
from uuid import UUID

from app.config import settings
from app.exceptions import ModelConfigurationVerificationError
from app.models.agent import Agent as StoredAgent
from app.models.internal import Agent
from app.schemas.agent_activation_models import (
    ActivationModelRuntime, AgentActivationModel, AgentActivationModelQuery,
)


class ModelOwner(Protocol):
    async def get_agent_activation_model(self, query: AgentActivationModelQuery) -> AgentActivationModel: ...


async def verify_model_configuration(
    stored: StoredAgent, agent: Agent, actor: UUID, owner: ModelOwner, *, verify_runtime: bool = True,
) -> Agent:
    query = AgentActivationModelQuery(project_id=stored.project_id, requested_by=actor,
        model=stored.model, provider_id=stored.llm_provider_id)
    proof = await owner.get_agent_activation_model(query)
    if (proof.model_dump(include=set(AgentActivationModelQuery.model_fields)) != query.model_dump()
            or proof.status != "ready" or not proof.owner_revision or not proof.runtime_revision):
        raise ModelConfigurationVerificationError(
            "当前模型配置不可用或尚未同步，请检查模型设置后重新测试"
        )
    if verify_runtime or proof.source == "tenant":
        credentials = agent.llm_provider_credentials
        if credentials is None or proof.effective_model != agent.model:
            raise ModelConfigurationVerificationError(
                "模型配置尚未同步，请稍后重新测试"
            )
        runtime = ActivationModelRuntime.model_validate({"model": agent.model, **credentials.model_dump()})
        if not hmac.compare_digest(runtime.private_revision(settings.secret_key), proof.runtime_revision):
            raise ModelConfigurationVerificationError(
                "模型配置已变更或尚未同步，请重新测试"
            )
    return agent.model_copy(update={"model": proof.effective_model, "model_owner_revision": proof.owner_revision})
