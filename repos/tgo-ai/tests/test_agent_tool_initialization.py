"""Regression tests for fast, lazy agent tool initialization."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models.internal import AgentTool
from app.runtime.tools.builder.agent_builder import AgentBuilder
from app.runtime.tools.config import ToolsRuntimeSettings


@pytest.mark.asyncio
async def test_store_tool_uses_persisted_schema_without_remote_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder = AgentBuilder(ToolsRuntimeSettings())
    remote_discovery = AsyncMock()
    monkeypatch.setattr(
        builder,
        "_fetch_mcp_tools_from_endpoint",
        remote_discovery,
    )
    tool = AgentTool(
        tool_id=uuid4(),
        tool_name="express_service",
        tool_type="MCP",
        transport_type="http",
        endpoint="https://store.example.test/express/http",
        tool_source_type="STORE",
        enabled=True,
        base_config={
            "methods": {
                "query": {
                    "description": "查询物流",
                    "params": [
                        {
                            "name": "tracking_no",
                            "type": "str",
                            "required": True,
                        }
                    ],
                },
                "list_companies": {
                    "description": "快递公司列表",
                    "params": [],
                },
            }
        },
    )

    functions, stdio_commands = await builder._build_mcp_server_instances(
        {tool.endpoint: [tool]},
        {"X-API-Key": "test-key"},
    )

    assert stdio_commands == []
    assert [function.name for function in functions] == [
        "query",
        "list_companies",
    ]
    remote_discovery.assert_not_awaited()
