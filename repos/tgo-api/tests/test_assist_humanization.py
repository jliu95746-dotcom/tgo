from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.humanization_service import rewrite_assist_draft


@pytest.fixture(autouse=True)
def isolate_expression_tests(monkeypatch):
    # Semantic audit has its own tests; these exercise expression retries.
    monkeypatch.setattr("app.services.humanization_service.audit_reply_facts", AsyncMock(return_value=[]))


@pytest.mark.asyncio
async def test_rewrite_assist_draft_uses_tool_free_plain_text_pass() -> None:
    client = SimpleNamespace(
        run_supervisor_agent=AsyncMock(
            return_value={"content": "还不能确定有没有红色小羊皮款。"}
        )
    )

    result = await rewrite_assist_draft(
        client,
        project_id="project-1",
        agent_id="agent-1",
        customer_message="有没有红色小羊皮女包？",
        factual_draft=(
            "感谢您的耐心等待！我查了一下知识库，目前暂未找到同时满足"
            "红色与小羊皮材质的女包。"
        ),
        humanization_prompt="少用客套话，直接回答。",
    )

    assert result == "还不能确定有没有红色小羊皮款。"
    kwargs = client.run_supervisor_agent.await_args.kwargs
    assert kwargs["enable_memory"] is False
    assert kwargs["disable_tools"] is True
    assert kwargs["cancel_on_disconnect"] is True
    assert kwargs["markdown"] is False
    assert kwargs["temperature"] == 0.2
    assert kwargs["session_id"] is None
    assert "感谢您的耐心等待" in kwargs["message"]
    assert "少用客套话" in kwargs["system_message"]
    assert "只输出" in kwargs["system_message"]


@pytest.mark.asyncio
async def test_rewrite_assist_draft_rejects_empty_result() -> None:
    client = SimpleNamespace(
        run_supervisor_agent=AsyncMock(return_value={"content": "   "})
    )

    with pytest.raises(ValueError, match="empty"):
        await rewrite_assist_draft(
            client,
            project_id="project-1",
            agent_id=None,
            customer_message="有货吗？",
            factual_draft="暂时没货。",
        )


@pytest.mark.asyncio
async def test_rewrite_assist_draft_repairs_internal_knowledge_status_language(
) -> None:
    client = SimpleNamespace(
        run_supervisor_agent=AsyncMock(
            side_effect=[
                {"content": "部分款式的材质信息暂未明确列出。"},
                {
                    "content": (
                        "这款是不是小羊皮，还不能确定。"
                    )
                },
            ]
        )
    )

    result = await rewrite_assist_draft(
        client,
        project_id="project-1",
        agent_id="agent-1",
        customer_message="我想找一款红色小羊皮的包",
        factual_draft="部分款式的材质信息暂未明确列出。",
    )

    assert "暂未明确列出" not in result
    assert result == "这款是不是小羊皮，还不能确定。"
    assert client.run_supervisor_agent.await_count == 2
    repair_prompt = client.run_supervisor_agent.await_args.kwargs[
        "system_message"
    ]
    assert "process_language" in repair_prompt


@pytest.mark.asyncio
async def test_rewrite_assist_draft_never_returns_internal_status_language(
) -> None:
    client = SimpleNamespace(
        run_supervisor_agent=AsyncMock(
            return_value={"content": "部分款式的材质信息暂未明确列出。"}
        )
    )

    with pytest.raises(ValueError, match="process_language"):
        await rewrite_assist_draft(
            client,
            project_id="project-1",
            agent_id="agent-1",
            customer_message="这款是什么材质？",
            factual_draft="部分款式的材质信息暂未明确列出。",
        )
    assert client.run_supervisor_agent.await_count == 2
