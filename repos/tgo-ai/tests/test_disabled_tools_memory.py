"""A tool-free run must not regain tools through Agno memory features."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.runtime.tools.builder.agent_builder import AgentBuilder
from app.runtime.tools.config import ToolsRuntimeSettings
from app.runtime.tools.models import AgentConfig, AgentRunRequest


@pytest.mark.asyncio
@pytest.mark.parametrize("disabled", [True, False])
async def test_disabled_tools_keep_history_but_disable_memory_writes(monkeypatch, disabled):
    builder = AgentBuilder(ToolsRuntimeSettings())
    build_tools = AsyncMock(return_value=[])
    monkeypatch.setattr(builder, "_build_tools", build_tools)
    monkeypatch.setattr(builder, "_initialize_model", Mock(return_value=object()))
    monkeypatch.setattr(builder, "_ensure_memory_backend", Mock(return_value=(object(), object())))
    monkeypatch.setattr(
        "app.runtime.tools.builder.agent_builder.Agent",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )
    agent = await builder.build_agent(AgentRunRequest(
        message="确认订单号", config=AgentConfig(enable_memory=True),
        enable_memory=True, disable_tools=disabled, skills_enabled=False,
    ))
    assert agent.enable_agentic_memory is (not disabled)
    assert agent.enable_user_memories is (not disabled)
    assert agent.add_history_to_context is True
    assert agent.add_memories_to_context is True
    if disabled:
        build_tools.assert_not_awaited()
        assert agent.tools == []
    else:
        build_tools.assert_awaited_once()
