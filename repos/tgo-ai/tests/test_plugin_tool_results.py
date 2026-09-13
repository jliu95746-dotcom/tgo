"""Plugin data and failure semantics must survive both execution paths."""

import json
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from agno.tools.function import FunctionCall
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import tools
from app.database import get_db
from app.dependencies import get_current_or_internal_project_id
from app.models.tool import Tool, ToolType
from app.runtime.tools.utils import create_plugin_tool
from app.services.tool_executor import ToolExecutor


def plugin_tool():
    return Tool(
        id=uuid4(), project_id=uuid4(), name="fixture_query", tool_type=ToolType.MCP,
        transport_type="plugin", config={"plugin_id": "fixture-plugin", "tool_name": "query"},
    )


@pytest.fixture
def plugin_reply(monkeypatch):
    call = AsyncMock()
    monkeypatch.setattr("app.services.api_service.api_service_client.execute_plugin_tool", call)
    return call


@pytest.mark.asyncio
@pytest.mark.parametrize("reply,expected", [
    ({"success": True, "content": "文字结果"}, "文字结果"),
    ({"success": True, "content": ""}, ""),
    ({"success": True, "data": {}}, {}),
    ({"success": True, "data": {"status": "in_transit"}}, {"status": "in_transit"}),
    ({"success": True, "content": "查询结果", "data": {"status": "in_transit"}},
     {"content": "查询结果", "data": {"status": "in_transit"}}),
])
@pytest.mark.parametrize("path", ["direct", "agent"])
async def test_plugin_results_preserve_text_and_structured_data(plugin_reply, reply, expected, path):
    plugin_reply.return_value = reply
    tool = plugin_tool()
    if path == "direct":
        output = await ToolExecutor(cast(AsyncSession, object()), tool.project_id)._execute_plugin(tool, {})
    else:
        function = create_plugin_tool("fixture-plugin", "query", "查询", None, None)
        execution = await FunctionCall(function=function, arguments={}).aexecute()
        assert execution.status == "success"
        output = execution.result
    assert isinstance(output, str)
    assert (json.loads(output) if isinstance(expected, dict) else output) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [
    {"success": False, "error": "查询失败"},
    {"success": False, "content": "查询失败"},
    {"success": "false", "content": "不可当成成功"},
    {"success": 1, "content": "不可当成成功"},
    {"content": "缺少成功标志"},
    {"success": True, "content": {"unexpected": "object"}},
    {"success": True, "content": None},
])
async def test_plugin_failure_is_failure_in_both_execution_paths(plugin_reply, reply):
    plugin_reply.return_value = reply
    tool = plugin_tool()
    output = await ToolExecutor(cast(AsyncSession, object()), tool.project_id)._execute_plugin(tool, {})
    assert isinstance(output, str) and output.startswith("<error>")
    function = create_plugin_tool("fixture-plugin", "query", "查询", None, None)
    execution = await FunctionCall(function=function, arguments={}).aexecute()
    assert execution.status == "failure"


@pytest.mark.asyncio
@pytest.mark.parametrize("config", [None, {}, {"plugin_id": "fixture-plugin"}])
async def test_missing_plugin_configuration_is_explicit_and_never_calls_network(plugin_reply, config):
    tool = plugin_tool()
    tool.config = config
    output = await ToolExecutor(cast(AsyncSession, object()), tool.project_id)._execute_plugin(tool, {})
    assert output == "<error>Plugin tool missing configuration (plugin_id or tool_name)</error>"
    plugin_reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_saved_plugin_direct_endpoint_keeps_structured_output(db_session, plugin_reply):
    tool = plugin_tool()
    db_session.add(tool)
    await db_session.commit()
    plugin_reply.return_value = {"success": True, "data": {"status": "in_transit", "events": []}}
    app = FastAPI()
    app.include_router(tools.router)
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_or_internal_project_id] = lambda: tool.project_id
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/tools/{tool.id}/execute", json={"input_data": {"tracking_no": "FIXTURE"}})
    assert response.status_code == 200
    assert response.json() == {
        "success": True, "output_data": {"status": "in_transit", "events": []}, "error": None,
    }
