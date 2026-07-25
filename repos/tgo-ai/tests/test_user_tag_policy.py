"""Customer tags use one controlled vocabulary across every channel."""

from __future__ import annotations

import pytest

from app.runtime.tools.custom.user_tag import create_user_tag_tool


@pytest.mark.asyncio
async def test_user_tag_tool_rejects_unmanaged_tag_names() -> None:
    tool = create_user_tag_tool(
        agent_id="agent-1",
        session_id="session-1",
        user_id="visitor-1",
        project_id="project-1",
        request_id="request-1",
    )

    result = await tool.entrypoint(
        tags=[{"name": "made_up_by_model", "name_zh": "模型临时发明"}]
    )

    assert "不支持的标签" in result
    assert "made_up_by_model" in result
