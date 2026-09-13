"""Own saved MCP connections so failed initialization cannot leak cancel scopes."""

import sys
from contextlib import AsyncExitStack
from datetime import timedelta
from types import TracebackType
from typing import Awaitable, Callable, Literal, cast

from agno.tools.mcp import MCPTools
from agno.tools.function import Function, ToolResult
from agno.utils.mcp import get_entrypoint_for_tool
from mcp import ClientSession, StdioServerParameters
from mcp.types import CallToolResult, Tool as MCPTool
from pydantic import JsonValue
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client
from app.services.mcp_result import mcp_result_text
from app.runtime.tools.mcp_lifecycle import own_mcp_connection


class _CompletedCall:
    """Let the SDK format rich content without making a second network call."""

    def __init__(self, result: CallToolResult) -> None:
        self.result = result

    async def send_ping(self) -> None:
        return None

    async def call_tool(self, name: str, arguments: dict[str, JsonValue]) -> CallToolResult:
        return self.result


class SavedMCPTools(MCPTools):
    def __init__(
        self, url: str, transport: Literal["http", "sse", "stdio"],
        headers: dict[str, str], tool_names: list[str],
    ) -> None:
        if transport == "stdio":
            super().__init__(command=url, transport="stdio", include_tools=tool_names)
        else:
            super().__init__(url=url, transport="streamable-http" if transport == "http" else "sse",
                             include_tools=tool_names)
        self._saved_url = url
        self._saved_transport = transport
        self._saved_headers = headers
        self._owned_stack: AsyncExitStack | None = None

    async def build_tools(self) -> None:
        await super().build_tools()
        for function in self.functions.values():
            function.entrypoint = self._checked_entrypoint(function)

    def _checked_entrypoint(self, function: Function) -> Callable[..., Awaitable[ToolResult]]:
        tool = MCPTool(name=function.name, description=function.description,
                       inputSchema=function.parameters or {"type": "object"})

        async def execute(**arguments: JsonValue) -> ToolResult:
            if self.session is None:
                raise RuntimeError("MCP session is not connected")
            result = await self.session.call_tool(tool.name, arguments)
            text = mcp_result_text(result)  # Raise before the SDK can turn errors into text.
            formatter = get_entrypoint_for_tool(tool, cast(ClientSession, _CompletedCall(result)))
            formatted: ToolResult = await formatter(**arguments)
            if result.structuredContent is not None:
                formatted.content = text
            return formatted

        return execute

    # MCPTools is async, although its Toolkit ancestor declares sync hooks.
    async def connect(self, force: bool = False) -> None:  # type: ignore[override]
        if self.initialized and not force:
            return
        if force:
            await self.close()
        stack = AsyncExitStack()
        try:
            if self._saved_transport == "stdio":
                if not isinstance(self.server_params, StdioServerParameters):
                    raise ValueError("Stdio MCP requires process parameters")
                read, write = await stack.enter_async_context(stdio_client(self.server_params))
            elif self._saved_transport == "http":
                read, write, _ = await stack.enter_async_context(
                    streamablehttp_client(self._saved_url, headers=self._saved_headers))
            else:
                read, write = await stack.enter_async_context(
                    sse_client(self._saved_url, headers=self._saved_headers))
            self.session = await stack.enter_async_context(ClientSession(
                read, write, read_timeout_seconds=timedelta(seconds=self.timeout_seconds)))
            # MCPTools.initialize/connect catch BaseException, including
            # cancellation. Use the protocol directly and retain its errors.
            await self.session.initialize()
            await self.build_tools()
            self._initialized = True
            self._owned_stack = stack
            own_mcp_connection(self)
        except BaseException:
            try:
                await stack.__aexit__(*sys.exc_info())
            finally:
                self.session = None
                self._initialized = False
            raise

    async def close(self) -> None:  # type: ignore[override]
        stack, self._owned_stack = self._owned_stack, None
        try:
            if stack is not None:
                await stack.aclose()
        finally:
            self.session = None
            self._initialized = False

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()
