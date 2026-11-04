"""Authenticated MCP tools keep their schema, execution policy and failures."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from agno.tools import Function
from mcp import McpError
from mcp.types import ErrorData, ListToolsResult, Tool

from app.runtime.tools.builder.agent_builder import AgentBuilder
from app.runtime.tools.config import ToolsRuntimeSettings
from app.runtime.tools.models import MCPConfig
from app.runtime.tools.utils import wrap_mcp_authenticate_tool


@pytest.mark.asyncio
@pytest.mark.parametrize("synchronous", [False, True])
async def test_wrap_preserves_schema_policy_and_original(
    synchronous: bool,
) -> None:
    calls: list[str] = []

    def sync_lookup(order_no: str) -> str:
        calls.append(order_no)
        return "已发货"

    async def lookup(order_no: str) -> str:
        return sync_lookup(order_no)

    def hook() -> None:
        pass

    original = Function(
        name="order_query",
        description="查询指定订单",
        parameters={
            "type": "object",
            "properties": {"order_no": {"type": "string"}},
            "required": ["order_no"],
            "additionalProperties": False,
        },
        entrypoint=sync_lookup if synchronous else lookup,
        skip_entrypoint_processing=True,
        strict=True,
        requires_confirmation=True,
        requires_user_input=True,
        user_input_fields=["order_no"],
        external_execution=True,
        stop_after_tool_call=True,
        instructions="只能查询已授权客户订单",
        pre_hook=hook,
        post_hook=hook,
        tool_hooks=[hook],
    )
    wrapped = wrap_mcp_authenticate_tool(original)
    assert wrapped is not original
    assert wrapped.model_dump(exclude={"entrypoint"}) == original.model_dump(
        exclude={"entrypoint"},
    )
    assert wrapped.pre_hook is hook and wrapped.post_hook is hook
    assert wrapped.entrypoint is not None
    assert await wrapped.entrypoint(order_no="TEST-ORDER") == "已发货"
    assert original.entrypoint is (sync_lookup if synchronous else lookup)
    assert calls == ["TEST-ORDER"]


@pytest.mark.asyncio
@pytest.mark.parametrize("grouped", [False, True])
@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"message": "请登录", "url": "https://auth.example.test/start"},
         "请登录 https://auth.example.test/start"),
        ({"message": {"text": "请授权"}}, "请授权"),
        (None, "Interaction required"),
        (["unrecognized"], "Interaction required"),
        ({"message": {"text": ["invalid"]}, "url": {}},
         "Interaction required"),
    ],
)
async def test_interaction_required_has_usable_message(
    grouped: bool, data: object, expected: str,
) -> None:
    error = McpError(ErrorData(code=-32003, message="auth", data=data))
    raised = ExceptionGroup("transport", [error]) if grouped else error

    async def entrypoint() -> str:
        raise raised

    wrapped = wrap_mcp_authenticate_tool(
        Function(name="query", entrypoint=entrypoint),
    )
    assert wrapped.entrypoint is not None
    with pytest.raises(RuntimeError, match="") as caught:
        await wrapped.entrypoint()
    assert str(caught.value) == expected
    assert caught.value.__cause__ is raised


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [ValueError("failed"),
     McpError(ErrorData(code=-32600, message="invalid request")),
     ExceptionGroup("transport", [ValueError("unavailable")])],
)
async def test_non_auth_failure_is_not_reclassified(
    error: Exception,
) -> None:
    async def entrypoint() -> str:
        raise error

    wrapped = wrap_mcp_authenticate_tool(
        Function(name="query", entrypoint=entrypoint),
    )
    assert wrapped.entrypoint is not None
    with pytest.raises(type(error)) as caught:
        await wrapped.entrypoint()
    assert caught.value is error


@pytest.mark.asyncio
@pytest.mark.parametrize("grouped", [False, True, "mixed"])
async def test_cancellation_is_not_changed_to_authentication(
    grouped: bool | str,
) -> None:
    cancelled = asyncio.CancelledError()
    error = (
        BaseExceptionGroup("transport", [cancelled])
        if grouped else cancelled
    )
    if grouped == "mixed":
        error = BaseExceptionGroup("transport", [
            cancelled,
            McpError(ErrorData(code=-32003, message="auth")),
        ])

    async def entrypoint() -> str:
        raise error

    wrapped = wrap_mcp_authenticate_tool(
        Function(name="query", entrypoint=entrypoint),
    )
    assert wrapped.entrypoint is not None
    with pytest.raises(type(error)) as caught:
        await wrapped.entrypoint()
    assert caught.value is error


def test_missing_entrypoint_is_rejected_before_wrapping() -> None:
    with pytest.raises(ValueError, match="entrypoint"):
        wrap_mcp_authenticate_tool(Function(name="query"))


@pytest.mark.asyncio
async def test_authenticated_tool_is_not_silently_skipped_by_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @asynccontextmanager
    async def transport(
        *args: object, **kwargs: object,
    ) -> AsyncIterator[tuple[None, None, None]]:
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

        async def list_tools(self, cursor: str | None) -> ListToolsResult:
            return ListToolsResult(tools=[Tool(
                name="query", description="查询订单",
                inputSchema={"type": "object", "properties": {
                    "order_no": {"type": "string"},
                }, "required": ["order_no"]},
            )])

    module = "app.runtime.tools.builder.agent_builder."
    monkeypatch.setattr(module + "streamablehttp_client", transport)
    monkeypatch.setattr(module + "ClientSession", Session)
    builder = AgentBuilder(ToolsRuntimeSettings())
    monkeypatch.setattr(builder, "_build_mcp_headers", AsyncMock(
        return_value={"Authorization": "synthetic-test-value"},
    ))
    tools = await builder._build_mcp_tools(
        MCPConfig(url="https://mcp.example.test", auth_required=True),
        session_id=None, user_id=None,
    )
    assert len(tools) == 1
    assert tools[0].name == "query"
    assert tools[0].parameters["required"] == ["order_no"]
