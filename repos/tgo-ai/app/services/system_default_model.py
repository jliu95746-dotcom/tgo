"""Resolve the reserved follow-system model at run time, never in stored agents."""
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.internal import Agent
from app.models.llm_provider import LLMProvider
from app.models.project_ai_config import ProjectAIConfig
from app.runtime.tools.models import LLMProviderCredentials

SYSTEM_DEFAULT_MODEL = '__system_default__'

async def resolve_system_default_model(db: AsyncSession, agent: Agent) -> Agent:
    from app.services.platform_models import current_model, credentials
    platform = current_model()
    if platform is not None:
        if agent.model not in {SYSTEM_DEFAULT_MODEL, platform.model}:
            raise ValueError('该模型未获平台批准，请选择系统默认模型')
        return agent.model_copy(update={'model': platform.model, 'llm_provider_credentials': credentials(platform)})
    if agent.model != SYSTEM_DEFAULT_MODEL:
        return agent
    project_id = UUID(str(agent.project_id))
    result = await db.execute(select(ProjectAIConfig).where(
        ProjectAIConfig.project_id == project_id,
    ))
    config = result.scalar_one_or_none()
    if not config or not config.default_chat_provider_id or not config.default_chat_model:
        raise ValueError('尚未配置系统默认客服模型，请到模型配置中设置')
    provider_result = await db.execute(select(LLMProvider).where(
        LLMProvider.id == config.default_chat_provider_id,
        LLMProvider.project_id == project_id,
        LLMProvider.is_active.is_(True),
        LLMProvider.deleted_at.is_(None),
    ))
    provider = provider_result.scalar_one_or_none()
    if not provider or not provider.api_key:
        raise ValueError('系统默认模型服务不可用或尚未同步，请检查模型配置')
    return agent.model_copy(update={
        'model': config.default_chat_model,
        'llm_provider_credentials': LLMProviderCredentials(
            provider_kind=provider.provider_kind, vendor=provider.vendor,
            api_base_url=provider.api_base_url, api_key=provider.api_key,
            organization=provider.organization, timeout=provider.timeout,
        ),
    })
