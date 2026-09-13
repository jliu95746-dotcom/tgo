"""MCP failure and structured-output contracts across tool execution paths."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import AsyncIterator, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from agno.models.response import ToolExecution
from agno.tools.function import Function, FunctionCall as AgnoFunctionCall
from mcp.types import CallToolResult, TextContent, Tool as MCPTool
from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tool import Tool, ToolType
from app.runtime.tools.builder.agent_builder import StoreRemoteAgent
from app.runtime.tools.utils import create_agno_mcp_tool
from app.schemas.chat import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    ChoiceMessage,
    FunctionCall,
    ToolCall,
)
from app.services.chat_service import ChatService
from app.services.tool_executor import ToolExecutor, _extract_store_output


@pytest.fixture
def mcp_reply(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = SimpleNamespace(reply=CallToolResult(content=[]), calls=[])

    @asynccontextmanager
    async def transport(*args: object, **kwargs: object) -> AsyncIterator[tuple[None, None, None]]:
        yield None, None, None

    class Session:
        def __init__(self, *args: object) -> None:
            pass

        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def initialize(self) -> None:
            pass

        async def call_tool(self, name: str, arguments: object) -> CallToolResult:
            state.calls.append((name, arguments))
            return cast(CallToolResult, state.reply)

    for module in ("app.services.tool_executor", "app.runtime.tools.utils"):
        monkeypatch.setattr(module + ".streamablehttp_client", transport)
        monkeypatch.setattr(module + ".ClientSession", Session)
    return state


def executor_with_tool() -> tuple[ToolExecutor, Tool]:
    tool = Tool(
        id=uuid4(),
        project_id=uuid4(),
        name="query",
        tool_type=ToolType.MCP,
        transport_type="http",
        endpoint="https://mcp.example.test/mcp",
    )
    executor = ToolExecutor(cast(AsyncSession, object()), tool.project_id)
    executor._tool_registry[tool.name] = {"type": "mcp", "tool": tool}
    return executor, tool


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [[], [TextContent(type="text", text="查询失败")]])
async def test_direct_mcp_error_never_becomes_success(
    mcp_reply: SimpleNamespace,
    content: list[TextContent],
) -> None:
    mcp_reply.reply = CallToolResult(isError=True, content=content)
    executor, tool = executor_with_tool()
    output = await executor.execute(tool.name, {"tracking_no": "SF1234567890"})
    assert output.startswith("<error>MCP execution failed:")
    assert "successfully" not in output
    assert mcp_reply.calls == [("query", {"tracking_no": "SF1234567890"})]


@pytest.mark.asyncio
@pytest.mark.parametrize("structured", [{}, {"status": "in_transit", "events": []}])
async def test_direct_mcp_preserves_structured_only_results(
    mcp_reply: SimpleNamespace,
    structured: dict[str, JsonValue],
) -> None:
    mcp_reply.reply = CallToolResult(content=[], structuredContent=structured)
    executor, tool = executor_with_tool()
    output = await executor.execute(tool.name, {})
    assert json.loads(output) == structured


@pytest.mark.asyncio
async def test_direct_mcp_rejects_unsupported_transport_without_network(
    mcp_reply: SimpleNamespace,
) -> None:
    executor, tool = executor_with_tool()
    tool.transport_type = "stdio"
    output = await executor.execute(tool.name, {})
    assert output == "<error>MCP direct execution supports HTTP or SSE transport only</error>"
    assert mcp_reply.calls == []


@pytest.mark.parametrize(
    "payload",
    [
        {"isError": True},
        {"isError": True, "structuredContent": {"message": "查询失败"}},
        {"isError": True, "content": []},
    ],
)
def test_store_failure_flag_is_preserved_without_text(payload: dict[str, JsonValue]) -> None:
    output = _extract_store_output({"jsonrpc": "2.0", "id": 1, "result": payload})
    assert output.startswith("<error>Store execution failed:")


def test_store_structured_payload_is_not_dropped_for_display_text() -> None:
    payload = {"status": "in_transit", "events": []}
    output = _extract_store_output(
        {
            "result": {
                "structuredContent": payload,
                "content": [{"type": "text", "text": "查询结果"}],
            }
        }
    )
    assert json.loads(output) == payload


@pytest.mark.parametrize("payload", [{}, {"status": "in_transit"}])
def test_store_structured_only_output(payload: dict[str, JsonValue]) -> None:
    assert json.loads(_extract_store_output({"structuredContent": payload})) == payload


def test_store_structured_error_retains_reason_without_content() -> None:
    output = _extract_store_output({"isError": True, "structuredContent": {"message": "查询失败"}})
    assert output == '<error>Store execution failed: {"message": "查询失败"}</error>'


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("content", [[], [TextContent(type="text", text="第一段"), TextContent(type="text", text="第二段")]])
async def test_agent_wrapper_reports_actual_execution_status(
    mcp_reply: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    failed: bool,
    content: list[TextContent],
) -> None:
    mcp_reply.reply = CallToolResult(content=content, isError=failed)
    usage = AsyncMock()
    monkeypatch.setattr("app.runtime.tools.utils._record_tool_usage", usage)
    tool = create_agno_mcp_tool(
        MCPTool(name="query", inputSchema={"type": "object"}),
        "https://mcp.example.test/mcp",
    )
    execution = await AgnoFunctionCall(function=tool, arguments={}).aexecute()
    assert execution.status == ("failure" if failed else "success")
    assert usage.await_count == 1
    assert usage.call_args.kwargs["status"] == ("error" if failed else "success")
    if failed:
        assert usage.call_args.kwargs["error_message"]
    elif content:
        assert execution.result == "第一段\n第二段"


@pytest.mark.asyncio
async def test_agent_wrapper_preserves_structured_only_output(
    mcp_reply: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mcp_reply.reply = CallToolResult(content=[], structuredContent={"status": "in_transit"})
    monkeypatch.setattr("app.runtime.tools.utils._record_tool_usage", AsyncMock())
    tool = create_agno_mcp_tool(MCPTool(name="query", inputSchema={}), "https://mcp.example.test/mcp")
    execution = await AgnoFunctionCall(function=tool, arguments={}).aexecute()
    assert execution.status == "success"
    assert json.loads(execution.result) == {"status": "in_transit"}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["exception", "missing", "no_entrypoint", "not_callable", "error_output", "success"])
async def test_remote_tool_replay_preserves_failure_status(kind: str) -> None:
    async def run() -> str:
        if kind == "exception":
            raise ValueError("查询失败")
        return "<error>查询失败</error>" if kind == "error_output" else "运输中"

    registered: dict[str, object] = {"query": Function(name="query", entrypoint=run)}
    if kind == "missing":
        registered = {}
    elif kind == "no_entrypoint":
        registered["query"] = Function(name="query")
    elif kind == "not_callable":
        registered["query"] = object()
    agent = cast(StoreRemoteAgent, SimpleNamespace(_tool_map=registered))
    call = ToolExecution(tool_name="query", tool_args={}, external_execution_required=True)
    results = await StoreRemoteAgent._execute_tools_locally(agent, [call])
    assert len(results) == 1
    assert results[0].tool_call_error is (kind != "success")
    assert results[0].external_execution_required is False


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_chat_loop_passes_tool_failure_to_next_model_round(
    mcp_reply: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    stream: bool,
) -> None:
    mcp_reply.reply = CallToolResult(content=[TextContent(type="text", text="查询失败")], isError=True)
    executor, tool = executor_with_tool()

    async def register(instance: ToolExecutor, *args: object) -> None:
        instance._tool_registry = executor._tool_registry

    monkeypatch.setattr(ToolExecutor, "register_tools", register)
    service = ChatService(cast(AsyncSession, object()))
    monkeypatch.setattr(
        service, "_get_provider", AsyncMock(return_value=SimpleNamespace(provider_kind="openai_compatible"))
    )
    monkeypatch.setattr(service, "_merge_tools", AsyncMock(return_value=[]))
    call = ToolCall(id="call_query", function=FunctionCall(name="query", arguments="{}"))
    request = ChatCompletionRequest(
        provider_id=uuid4(),
        model="owned-fixture",
        messages=[ChatMessage(role="user", content="查快递")],
        tool_ids=[tool.id],
        auto_execute_tools=True,
        max_tool_rounds=2,
    )
    if stream:
        rounds = 0

        async def model_stream(*args: object) -> AsyncIterator[str]:
            nonlocal rounds
            rounds += 1
            delta = {"tool_calls": [call.model_dump()]} if rounds == 1 else {"content": "测试完成"}
            yield "data: " + json.dumps({"object": "chat.completion.chunk", "choices": [{"delta": delta}]}) + "\n\n"
            yield "data: [DONE]\n\n"

        monkeypatch.setattr(service, "_openai_stream", model_stream)
        chunks = [chunk async for chunk in service.create_completion_stream(request, tool.project_id)]
        assert rounds == 2
        assert sum(chunk == "data: [DONE]\n\n" for chunk in chunks) == 1
    else:
        completion = AsyncMock(
            side_effect=[
                ChatCompletionResponse(
                    model=request.model, choices=[Choice(index=0, message=ChoiceMessage(tool_calls=[call]))]
                ),
                ChatCompletionResponse(
                    model=request.model, choices=[Choice(index=0, message=ChoiceMessage(content="测试完成"))]
                ),
            ]
        )
        monkeypatch.setattr(service, "_openai_completion", completion)
        await service.create_completion(request, tool.project_id)
        assert completion.await_count == 2
    outputs = [message.content for message in request.messages if message.role == "tool"]
    assert len(outputs) == 1
    assert outputs[0].startswith("<error>MCP execution failed:")
