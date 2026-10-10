"""Current-turn evidence must supersede remembered business facts."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models.internal import Agent, AgentCollection, AgentExecutionContext
from app.schemas.knowledge import KnowledgeChannel
from app.services.current_knowledge import retrieve_current_knowledge
from app.runtime.supervisor.agents.builder import AgnoAgentBuilder
from app.runtime.tools.builder.agent_builder import AgentBuilder


def context(message="这款多少钱？", **overrides):
    now = datetime.now(timezone.utc)
    collection = str(uuid4())
    agent = Agent(
        id=uuid4(),
        name="客服",
        instruction="旧价格199元",
        model="test",
        collections=[
            AgentCollection(
                id=uuid4(),
                display_name="资料",
                collection_id=collection,
                enabled=True,
            )
        ],
        tools=[],
        workflows=[],
        created_at=now,
        updated_at=now,
    )
    return AgentExecutionContext(
        agent=agent,
        project_id=str(uuid4()),
        message=message,
        request_id="test",
        timeout=30,
        rag_url="http://rag",
        knowledge_channel=KnowledgeChannel.WECOM_KF,
        require_current_knowledge=True,
        **overrides,
    )


def result(ctx, text, score=0.8):
    return {
        "results": [
            {
                "document_id": str(uuid4()),
                "file_id": str(uuid4()),
                "collection_id": ctx.agent.collections[0].collection_id,
                "content": text,
                "relevance_score": score,
            }
        ]
    }


@pytest.mark.asyncio
async def test_same_session_retrieves_again_and_preserves_full_new_conditions():
    ctx = context(knowledge_context=["晨光包多少钱？"])
    client = SimpleNamespace(
        search_documents=AsyncMock(
            side_effect=[
                result(ctx, "晨光包199元，七天退换。"),
                result(ctx, "晨光包299元，十五天退换，但使用后不退。"),
            ]
        )
    )
    first = await retrieve_current_knowledge(ctx, client=client)
    second = await retrieve_current_knowledge(ctx, client=client)
    assert "199" in first.documents[0].content
    assert "299" in second.documents[0].content
    assert "使用后不退" in second.documents[0].content
    assert client.search_documents.await_count == 2
    assert "晨光包" in client.search_documents.call_args.kwargs["query"]


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["你好！", "谢谢", "好的", "再见", "Hi"])
async def test_pure_social_turns_skip_retrieval(message):
    client = SimpleNamespace(search_documents=AsyncMock())
    evidence = await retrieve_current_knowledge(
        context(message), client=client
    )
    assert evidence.status == "skipped"
    client.search_documents.assert_not_awaited()


@pytest.mark.asyncio
async def test_mixed_message_retrieves_and_each_enabled_collection_once():
    ctx = context("你好，退换政策变了吗？")
    ctx.agent.collections += [
        ctx.agent.collections[0],
        AgentCollection(
            id=uuid4(),
            display_name="停用",
            collection_id=str(uuid4()),
            enabled=False,
        ),
    ]
    client = SimpleNamespace(
        search_documents=AsyncMock(return_value=result(ctx, "十五天退换。"))
    )
    evidence = await retrieve_current_knowledge(ctx, client=client)
    assert evidence.status == "matched"
    client.search_documents.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["timeout", "empty", "oversize", "invalid_scope"]
)
async def test_retrieval_failures_never_admit_old_or_partial_facts(mode):
    ctx = context()
    payload = (
        result(ctx, "条件" * 6000) if mode == "oversize" else {"results": []}
    )
    if mode == "invalid_scope":
        payload = result(ctx, "旧价格199元")
        payload["results"][0]["collection_id"] = str(uuid4())
    client = SimpleNamespace(
        search_documents=AsyncMock(
            side_effect=TimeoutError() if mode == "timeout" else None,
            return_value=payload,
        )
    )
    evidence = await retrieve_current_knowledge(ctx, client=client)
    assert evidence.status != "matched"
    assert not evidence.documents


@pytest.mark.asyncio
async def test_media_tool_disable_boundary_is_preserved():
    client = SimpleNamespace(search_documents=AsyncMock())
    evidence = await retrieve_current_knowledge(
        context(disable_tools=True), client=client
    )
    assert evidence.status == "unavailable"
    client.search_documents.assert_not_awaited()


@pytest.mark.asyncio
async def test_builder_reuses_evidence_and_only_removes_rag_capability(
    monkeypatch,
):
    ctx = context(enable_memory=True)
    search = AsyncMock(return_value=result(ctx, "晨光包299元，十五天退换。"))
    monkeypatch.setattr(
        "app.services.current_knowledge.rag_service_client.search_documents",
        search,
    )
    captured = []

    async def build(_self, request, internal_agent=None):
        captured.append(request)
        return SimpleNamespace(id="a", name="客服")

    monkeypatch.setattr(AgentBuilder, "build_agent", build)
    await AgnoAgentBuilder().build_agent(ctx)
    request = captured[0]
    assert request.config.rag is None
    assert request.disable_tools is False and request.enable_memory is True
    assert "299" in request.config.system_message
    assert ctx.agent.collections[0].enabled
    search.assert_awaited_once()
