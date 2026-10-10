"""Discover eligible tenant knowledge for an unbound default employee."""

from uuid import UUID
from typing import Literal

from app.models.internal import Agent, AgentCollection
from app.models.agent import Agent as StoredAgent
from app.schemas.knowledge import KnowledgeChannel
from app.schemas.knowledge_availability import AgentKnowledgeAvailability
from app.services.rag_service import rag_service_client


async def agent_knowledge_availability(
    agent: Agent | StoredAgent, channel: KnowledgeChannel
) -> AgentKnowledgeAvailability:
    if not agent.project_id:
        raise ValueError("无法确认知识库归属")
    project_id = str(agent.project_id)
    data = await rag_service_client.knowledge_availability(project_id, channel)
    if data.project_id != UUID(project_id):
        raise ValueError("知识库归属不一致")
    if data.channel != channel:
        raise ValueError("知识库渠道不一致")
    issues: list[str] = []
    if isinstance(agent, StoredAgent) and not agent.is_active:
        issues.append("inactive_agent")
    mode: Literal["project_default", "explicit", "unbound"]
    if agent.collections:
        mode = "explicit"
        enabled = {c.collection_id for c in agent.collections if c.enabled}
        collections = [c for c in data.collections if str(c.id) in enabled]
        if any(not c.enabled for c in agent.collections):
            issues.append("disabled_binding")
        if enabled - {str(c.id) for c in collections}:
            issues.append("missing_collection")
    elif agent.is_default or (
        isinstance(agent, StoredAgent) and agent.is_active
    ):
        mode, collections = "project_default", data.collections
    else:
        mode, collections = "unbound", []
        issues.append("no_binding")
    return AgentKnowledgeAvailability(
        project_id=data.project_id,
        channel=channel,
        agent_id=agent.id,
        binding_mode=mode,
        collections=collections,
        issues=issues,
    )


async def resolve_agent_knowledge(
    agent: Agent, channel: KnowledgeChannel
) -> Agent:
    if not agent.is_default or agent.collections:
        return agent
    report = await agent_knowledge_availability(agent, channel)
    bindings = [
        AgentCollection(
            id=c.id,
            collection_id=str(c.id),
            enabled=True,
            display_name=c.name,
            description=None,
        )
        for c in report.collections
        if c.eligible_chunk_count > 0
    ]
    return agent.model_copy(update={"collections": bindings})
