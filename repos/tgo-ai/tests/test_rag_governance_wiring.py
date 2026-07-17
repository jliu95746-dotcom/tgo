"""Tests for fail-closed RAG channel propagation."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.internal import Agent, AgentCollection, AgentExecutionContext
from app.runtime.supervisor.agents.builder import AgnoAgentBuilder
from app.runtime.tools.builder import agent_builder as agent_builder_module
from app.runtime.tools.builder.agent_builder import AgentBuilder
from app.runtime.tools.config import ToolsRuntimeSettings
from app.runtime.tools.models import RagConfig
from app.runtime.tools import utils as tool_utils
from app.schemas.agent_run import SupervisorRunRequest
from app.schemas.knowledge import KnowledgeChannel


def _context(channel: KnowledgeChannel | None) -> AgentExecutionContext:
    now = datetime(2026, 7, 17, tzinfo=UTC)
    agent = Agent(
        id=uuid4(),
        project_id=str(uuid4()),
        name="客服助手",
        instruction="回答客户问题",
        model="openai:gpt-4o",
        config={},
        tools=[],
        collections=[
            AgentCollection(
                id=uuid4(),
                collection_id=str(uuid4()),
                enabled=True,
                display_name="产品知识库",
            )
        ],
        workflows=[],
        created_at=now,
        updated_at=now,
    )
    return AgentExecutionContext(
        agent=agent,
        project_id=agent.project_id or str(uuid4()),
        message="退款政策是什么？",
        request_id="request-1",
        timeout=30,
        rag_url="http://tgo-rag:18082",
        knowledge_channel=channel,
    )


def test_supervisor_request_accepts_only_supported_knowledge_channels() -> None:
    request = SupervisorRunRequest(
        message="退款政策是什么？",
        knowledge_channel=KnowledgeChannel.WECOM_KF,
    )
    assert request.knowledge_channel is KnowledgeChannel.WECOM_KF

    with pytest.raises(ValidationError):
        SupervisorRunRequest(message="退款政策是什么？", knowledge_channel="unknown")


def test_supervisor_builder_carries_channel_into_rag_config() -> None:
    context = _context(KnowledgeChannel.WECOM_KF)
    builder = AgnoAgentBuilder(ToolsRuntimeSettings())

    config = builder._build_agent_config(context)

    assert config.rag is not None
    assert config.rag.knowledge_channel is KnowledgeChannel.WECOM_KF


@pytest.mark.asyncio
async def test_agent_builder_skips_rag_tools_when_channel_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_rag_tool = AsyncMock()
    monkeypatch.setattr(agent_builder_module, "create_rag_tool", create_rag_tool)
    builder = AgentBuilder(ToolsRuntimeSettings())

    tools = await builder._build_rag_tools(
        RagConfig(
            rag_url="http://tgo-rag:18082",
            project_id=str(uuid4()),
            collections=[str(uuid4())],
            knowledge_channel=None,
        )
    )

    assert tools == []
    create_rag_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_rag_tool_uses_automatic_answer_endpoint_and_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_posts: list[dict[str, object]] = []

    class FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self.payload = payload

        async def __aenter__(self) -> "FakeResponse":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        async def json(self) -> dict[str, object]:
            return self.payload

    class FakeSession:
        async def __aenter__(self) -> "FakeSession":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        def get(self, url: str, params: dict[str, str]) -> FakeResponse:
            return FakeResponse({"display_name": "产品知识库", "description": "政策"})

        def post(
            self,
            url: str,
            params: dict[str, str],
            json: dict[str, object],
        ) -> FakeResponse:
            captured_posts.append({"url": url, "params": params, "json": json})
            return FakeResponse({"results": []})

    monkeypatch.setattr(tool_utils.aiohttp, "ClientSession", FakeSession)
    collection_id = str(uuid4())
    project_id = str(uuid4())

    tool = await tool_utils.create_rag_tool(
        "http://tgo-rag:18082",
        collection_id,
        project_id,
        knowledge_channel=KnowledgeChannel.WECOM_KF,
    )
    result = await tool.entrypoint(query="退款政策")

    assert result == "<documents />"
    assert captured_posts == [
        {
            "url": (
                f"http://tgo-rag:18082/v1/collections/{collection_id}"
                "/documents/search/automatic-answer"
            ),
            "params": {"project_id": project_id},
            "json": {
                "query": "退款政策",
                "limit": 10,
                "filters": None,
                "channel": "wecom_kf",
            },
        }
    ]
