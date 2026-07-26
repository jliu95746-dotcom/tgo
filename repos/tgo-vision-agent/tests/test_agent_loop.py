"""Tests for structured AgentLoop completion results."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.domain.agent.agent_loop import AgentLoop
from app.domain.agent.entities import (
    Action,
    ActionType,
    AgentContext,
    AppState,
    LoginStatus,
    Observation,
)
from app.domain.agent.prompts.reasoning import ACTION_DECISION_PROMPT


def test_reasoning_prompt_formats_structured_examples():
    prompt = ACTION_DECISION_PROMPT.format(
        goal="提取未读联系人",
        observation="消息列表",
        history="无",
        available_apps="com.tencent.mm",
    )

    assert '{"contacts": []}' in prompt
    assert '"preview": "最新未读消息预览"' in prompt


@pytest.mark.asyncio
async def test_complete_action_returns_structured_data(mock_agentbay_controller):
    vision = AsyncMock()
    vision.analyze_screen.return_value = Observation(
        screen_type="conversation_list",
        app_state=AppState(login_status=LoginStatus.LOGGED_IN),
        raw_description="发现一位有未读消息的联系人",
    )
    reasoning = AsyncMock()
    reasoning.decide_action.return_value = Action(
        action_type=ActionType.COMPLETE,
        parameters={
            "contacts": [
                {
                    "id": "wx-user-1",
                    "name": "测试客户",
                    "preview": "你好",
                }
            ]
        },
        reasoning="提取完成",
    )
    agent = AgentLoop(
        reasoning=reasoning,
        vision=vision,
        controller=mock_agentbay_controller,
        session_id="test-session",
    )
    context = AgentContext(
        goal="提取未读联系人",
        app_type="wechat",
        session_id="test-session",
    )

    result = await agent.run(context)

    assert result.success is True
    assert result.data["login_status"] == "logged_in"
    assert result.data["contacts"][0]["preview"] == "你好"
