from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.humanization_service import rewrite_assist_draft


@pytest.mark.asyncio
async def test_rewrite_assist_draft_uses_tool_free_plain_text_pass() -> None:
    client = SimpleNamespace(
        run_supervisor_agent=AsyncMock(
            return_value={"content": "目前没有红色小羊皮的款式。您更看重颜色还是材质？"}
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

    assert result == "目前没有红色小羊皮的款式。您更看重颜色还是材质？"
    kwargs = client.run_supervisor_agent.await_args.kwargs
    assert kwargs["enable_memory"] is False
    assert kwargs["disable_tools"] is True
    assert kwargs["markdown"] is False
    assert kwargs["temperature"] == 0.4
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
