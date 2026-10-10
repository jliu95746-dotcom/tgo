"""Check channel readiness through the owning AI service."""

from uuid import UUID

from fastapi import HTTPException

from app.models import Platform
from app.schemas.knowledge_availability import AgentKnowledgeAvailability
from app.services.ai_client import ai_client
from app.services.knowledge_channel import resolve_platform_knowledge_channel


async def platform_knowledge_availability(
    platform: Platform, agent_id: UUID | None
) -> AgentKnowledgeAvailability:
    channel = resolve_platform_knowledge_channel(platform.type)
    result = await ai_client.knowledge_availability(
        str(platform.project_id), channel.value, agent_id
    )
    if result.project_id != platform.project_id or result.channel != channel:
        raise HTTPException(502, "无法确认知识库归属或适用渠道")
    if agent_id is not None and result.agent_id != agent_id:
        raise HTTPException(502, "无法确认 AI 客服绑定")
    return result
